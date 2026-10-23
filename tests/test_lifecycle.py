"""Server lifecycle: start/stop/restart jobs, crash handling, autostart and the schedule (FEAT-06)."""
from support import *  # noqa: F401,F403


def test_admin_action_routes_exist_for_dashboard_buttons():
    for action in ("op", "deop", "kick", "ban", "day", "night", "clear", "rain", "save"):
        response = client.post(f"/api/admin/{action}", auth=("admin", "s3cret-pass"), json={"player": "Steve"})
        assert response.status_code in {200, 409, 400}


def test_server_start_installs_before_launch(monkeypatch):
    class DummyProcess:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            return None

        def is_running(self):
            return True

        def send(self, command):
            return None

        def stop(self):
            return None

    calls = {}

    def fake_prepare(loader, version, target_dir, java_exe, log=print, progress=None):
        if progress:
            progress(50, 100)
        return None

    def fake_update_properties(server_dir, **kwargs):
        calls["server_dir"] = server_dir
        calls["kwargs"] = kwargs

    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: False)
    monkeypatch.setattr(downloaders, "prepare", fake_prepare)
    monkeypatch.setattr(java_manager, "ensure_java", lambda required, log=print: "java")
    monkeypatch.setattr(downloaders, "launch_args", lambda *args, **kwargs: ["java", "-jar", "server.jar"])
    monkeypatch.setattr(common_module, "ServerProcess", DummyProcess)
    monkeypatch.setattr(common_module, "update_properties", fake_update_properties)

    response = _start(json={"server_port": 25566, "motd": "Advanced test", "max_players": 12})
    assert response.status_code == 202
    assert response.json()["operation"] == "starting"
    assert app.state.runtime.operation_message == "Server started."
    assert app.state.runtime.install_status == "ready"
    assert app.state.runtime.install_progress >= 0
    assert calls["kwargs"]["server-port"] == 25566
    assert calls["kwargs"]["motd"] == "Advanced test"
    assert calls["kwargs"]["max-players"] == 12


def test_server_lifecycle_start_and_stop(fake_server):
    start_response = _start()
    assert start_response.status_code == 202
    assert start_response.json()["state"] == "starting"
    assert app.state.runtime.state == "running"

    command_response = client.post(
        "/api/server/command",
        auth=("admin", "s3cret-pass"),
        json={"command": "say hello"},
    )
    assert command_response.status_code == 200
    assert fake_server[0].sent == ["say hello"]

    stop_response = client.post("/api/server/stop", auth=("admin", "s3cret-pass"))
    assert stop_response.status_code == 200
    assert not fake_server[0].is_running()


def test_restart_waits_for_previous_process_to_exit(monkeypatch):
    events = []

    class StoppableProcess:
        def __init__(self, *args, **kwargs):
            self.running = False

        def start(self):
            self.running = True
            events.append("start")

        def is_running(self):
            return self.running

        def send(self, command):
            return None

        def stop(self):
            self.running = False
            events.append("stop")

        def kill(self):
            self.running = False

    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: True)
    monkeypatch.setattr(java_manager, "ensure_java", lambda required, log=print: "java")
    monkeypatch.setattr(downloaders, "launch_args", lambda *args, **kwargs: ["java"])
    monkeypatch.setattr(common_module, "update_properties", lambda server_dir, **kwargs: None)
    monkeypatch.setattr(common_module, "ServerProcess", StoppableProcess)

    previous = StoppableProcess()
    previous.start()
    app.state.runtime.process = previous
    events.clear()

    response = client.post("/api/server/restart", auth=("admin", "s3cret-pass"))
    assert response.status_code == 202
    _wait_idle()
    assert app.state.runtime.operation_message == "Server started."
    assert events == ["stop", "start"]
    assert app.state.runtime.process is not previous


def test_crash_triggers_automatic_restart(fake_server):
    _start()
    fake_server[0].exit(1)
    assert _wait_until(lambda: len(fake_server) == 2)
    assert app.state.runtime.process is fake_server[1]
    assert app.state.runtime.state == "running"


def test_crash_restart_pauses_after_limit(fake_server):
    _start()
    now = time.monotonic()
    app.state.runtime.crash_times = [now] * common_module.CRASH_RESTART_LIMIT
    fake_server[0].exit(1)
    time.sleep(0.2)
    assert len(fake_server) == 1
    assert app.state.runtime.state == "crashed"
    history = client.get("/api/console", auth=("admin", "s3cret-pass")).json()["lines"]
    assert any("automatic restart paused" in line for line in history)


def test_deliberate_stops_are_not_crashes(fake_server):
    _start()
    client.post("/api/server/stop", auth=("admin", "s3cret-pass"))
    fake_server[0].exit(1)

    _start()
    client.post("/api/server/command", auth=("admin", "s3cret-pass"), json={"command": "/stop"})
    fake_server[1].exit(143)
    time.sleep(0.2)
    assert len(fake_server) == 2
    assert app.state.runtime.state == "stopped"


def test_auto_restart_can_be_disabled(fake_server, monkeypatch):
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "auto_restart": False})
    _start()
    fake_server[0].exit(1)
    time.sleep(0.2)
    assert len(fake_server) == 1


def test_autostart_launches_server_and_playit(fake_server, fake_playit, fake_cloudflared, monkeypatch):
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config",
                        lambda: {**original(), "autostart_server": True, "autostart_playit": True})
    monkeypatch.delenv("EZ_NO_AUTOSTART", raising=False)
    playit.save_config("demo-secret", "")
    app.state.autostart()
    assert _wait_until(lambda: len(fake_server) == 1 and app.state.runtime.playit_agent is not None)
    assert _wait_until(lambda: playit.load_config().get("address") == "demo.example:25565")


def test_autostart_respects_kill_switch(fake_server, monkeypatch):
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "autostart_server": True})
    monkeypatch.setenv("EZ_NO_AUTOSTART", "1")
    app.state.autostart()
    time.sleep(0.2)
    assert fake_server == []


def test_shutdown_stops_server_and_tunnel(fake_server, fake_playit):
    _start()
    agent = DummyAgent()
    agent.start("s")
    app.state.runtime.playit_agent = agent
    app.state.shutdown()
    assert not fake_server[0].is_running()
    assert not agent.is_running()
    assert app.state.runtime.stop_requested is True
    app.state.runtime.shutting_down = False


def test_player_tracking_from_console_lines():
    runtime = common_module.RuntimeState()
    runtime.track_player_event("[12:00:00] [Server thread/INFO]: Steve joined the game")
    runtime.track_player_event("[12:00:01] [Server thread/INFO]: Alex joined the game")
    runtime.track_player_event("[12:00:02] [Server thread/INFO]: <Steve> Bob joined the game")
    runtime.track_player_event("[12:00:03] [Server thread/INFO]: Alex left the game")
    snapshot = runtime.snapshot()
    assert snapshot["players"] == 1
    assert snapshot["player_names"] == ["Steve"]


def test_create_returns_at_once_and_installs_in_background(slow_install):
    started = time.monotonic()
    response = client.post("/api/server/create", auth=("admin", "s3cret-pass"), json={"loader": "vanilla"})
    assert time.monotonic() - started < 2
    assert response.status_code == 202
    assert response.json()["operation"] == "creating"
    assert _wait_until(lambda: app.state.runtime.install_status == "installing")
    assert client.get("/api/status", auth=("admin", "s3cret-pass")).json()["operation"] == "installing"
    slow_install.set()
    _wait_idle()
    status = client.get("/api/status", auth=("admin", "s3cret-pass")).json()
    assert status["install_status"] == "ready" and status["operation"] == "idle"
    assert status["operation_message"] == "Server vanilla 1.21.7 is ready."


def test_second_lifecycle_request_while_busy_gets_409(slow_install):
    assert client.post("/api/server/create", auth=("admin", "s3cret-pass")).status_code == 202
    for method, url, body in (("post", "/api/server/create", None), ("post", "/api/server/start", None),
                              ("post", "/api/server/restart", None), ("post", "/api/server/stop", None),
                              ("post", "/api/server/backups/restore", {"backup_path": "x.zip"})):
        response = getattr(client, method)(url, auth=("admin", "s3cret-pass"), json=body)
        assert response.status_code == 409, url
        assert response.json()["detail"].startswith("Busy:")
    slow_install.set()


def test_concurrent_starts_launch_exactly_one_process(slow_install):
    results = []

    def press_start():
        results.append(client.post("/api/server/start", auth=("admin", "s3cret-pass")).status_code)

    threads = [threading.Thread(target=press_start) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [202, 409, 409, 409]
    slow_install.set()
    _wait_idle()
    assert len(RecordingProcess.instances) == 1
    assert app.state.runtime.state == "running"


def test_failed_install_is_reported_through_status(fake_server, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("download blocked")

    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: False)
    monkeypatch.setattr(downloaders, "prepare", broken)
    assert _start().status_code == 202
    status = client.get("/api/status", auth=("admin", "s3cret-pass")).json()
    assert status["install_status"] == "failed"
    assert status["state"] == "stopped" and status["operation"] == "idle"
    assert "download blocked" in status["operation_error"]
    history = client.get("/api/console", auth=("admin", "s3cret-pass")).json()["lines"]
    assert any("Server start failed: download blocked" in line for line in history)
    assert fake_server == []


def test_crash_restart_waits_for_running_job_then_skips_if_started(slow_install):
    runtime = app.state.runtime
    assert client.post("/api/server/start", auth=("admin", "s3cret-pass")).status_code == 202
    waiter = threading.Thread(target=app.state.restart_after_crash)  # what the crash timer runs
    waiter.start()
    time.sleep(0.2)
    assert waiter.is_alive()  # blocked on the lifecycle lock while the start installs
    slow_install.set()
    waiter.join(3)
    assert not waiter.is_alive()
    assert len(RecordingProcess.instances) == 1 and runtime.state == "running"


def test_install_banner_is_on_every_page():
    for section in ("overview", "setup", "advanced", "worlds", "mods", "remote", "backups", "console"):
        assert client.get(f"/dashboard/{section}").text.count('id="install-banner"') == 1, section


def test_daily_restart_warns_then_restarts_once_a_day(fake_server, schedule_runtime):
    config_store.save({"restart_daily_time": "05:00"})
    _start()
    schedule_runtime.online_players.add("Alex")
    tick = app.state.services.scheduler_tick
    for hhmm in ("04:45", "04:50", "04:51", "04:55", "04:56", "04:59"):
        tick(_at(hhmm))
    says = [c for c in fake_server[0].sent if c.startswith("say")]
    assert says == ["say Server restarting in 10 minutes.", "say Server restarting in 5 minutes.",
                    "say Server restarting in 1 minute."]
    tick(_at("05:00"))
    _wait_idle()
    assert len(fake_server) == 2 and fake_server[1].is_running() and not fake_server[0].is_running()
    tick(_at("05:01"))
    tick(_at("05:20"))
    _wait_idle()
    assert len(fake_server) == 2  # once a day
    schedule_runtime.online_players.add("Alex")
    tick(_at("04:50", day=23))
    assert fake_server[1].sent == ["say Server restarting in 10 minutes."]


def test_daily_restart_says_the_real_time_left_and_skips_when_late(fake_server, schedule_runtime):
    config_store.save({"restart_daily_time": "05:00"})
    _start()
    tick = app.state.services.scheduler_tick
    tick(_at("04:50"))  # nobody online: no warning
    schedule_runtime.online_players.add("Sam")
    tick(_at("04:57"))
    assert [c for c in fake_server[0].sent if c.startswith("say")] == ["say Server restarting in 3 minutes."]
    schedule_runtime.daily_restart_done = ""
    tick(_at("05:45"))  # dashboard was off at 05:00; too late now
    assert len(fake_server) == 1


def test_idle_server_stops_and_says_why(fake_server, schedule_runtime, discord_posts):
    client.put("/api/notifications", auth=AUTH, json={"discord_friends_url": FRIENDS_HOOK})
    config_store.save({"idle_stop_minutes": 15})
    _start()
    started = schedule_runtime.empty_since
    tick = app.state.services.scheduler_tick
    tick(datetime.datetime.fromtimestamp(started + 14 * 60))
    assert fake_server[0].is_running()
    schedule_runtime.track_player_event("[12:00:00] [Server thread/INFO]: Alex joined the game")
    tick(datetime.datetime.fromtimestamp(started + 40 * 60))
    assert fake_server[0].is_running() and schedule_runtime.empty_since is None  # someone is playing
    schedule_runtime.track_player_event("[12:00:00] [Server thread/INFO]: Alex left the game")
    tick(datetime.datetime.fromtimestamp(schedule_runtime.empty_since + 16 * 60))
    assert not fake_server[0].is_running()
    fake_server[0].exit(0)
    status = client.get("/api/status", auth=AUTH).json()
    assert status["state"] == "stopped" and status["stopped_reason"] == "idle"
    assert _wait_until(lambda: any("nobody was online for 15 minutes" in c for _, c in discord_posts))
    time.sleep(0.1)
    assert not any(c == "Server is offline." for _, c in discord_posts)  # told once, with the reason
    _start()
    assert client.get("/api/status", auth=AUTH).json()["stopped_reason"] == ""


def test_quiet_hours_wrap_midnight_and_block_autostart(fake_server, monkeypatch):
    config = {"quiet_hours_start": "23:00", "quiet_hours_end": "07:00"}
    assert common_module._in_quiet_hours(config, _at("23:30"))
    assert common_module._in_quiet_hours(config, _at("06:59"))
    assert not common_module._in_quiet_hours(config, _at("07:00"))
    assert not common_module._in_quiet_hours({"quiet_hours_start": "", "quiet_hours_end": "07:00"}, _at("03:00"))
    assert common_module._in_quiet_hours({"quiet_hours_start": "13:00", "quiet_hours_end": "14:00"}, _at("13:15"))
    monkeypatch.setattr(common_module, "_in_quiet_hours", lambda config, now=None: True)
    assert app.state.services.launch_when_free("autostart") is False
    assert fake_server == []


def test_schedule_settings_are_validated():
    for body in ({"restart_daily_time": "25:00"}, {"quiet_hours_start": "5am"}, {"idle_stop_minutes": -5}):
        assert client.put("/api/config", auth=AUTH, json=body).status_code == 422, body
    ok = client.put("/api/config", auth=AUTH, json={"restart_daily_time": "05:00", "quiet_hours_end": ""})
    assert ok.status_code == 200
    assert 'id="schedule-save-button"' in client.get("/dashboard/setup").text
