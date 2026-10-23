"""Email and Discord alerts (FEAT-05)."""
from support import *  # noqa: F401,F403


def test_crash_sends_email_and_repeats_are_throttled(fake_server, sent_emails):
    _start()
    fake_server[0].exit(1)
    assert _wait_until(lambda: any("crashed" in subject for subject, _ in sent_emails))
    assert _wait_until(lambda: any("back online" in subject for subject, _ in sent_emails))
    assert _wait_until(lambda: len(fake_server) == 2)
    fake_server[1].exit(1)  # same alert again within 10 minutes -> not emailed twice
    assert _wait_until(lambda: len(fake_server) == 3)
    time.sleep(0.2)
    assert sum("Minecraft server crashed" in subject for subject, _ in sent_emails) == 1


def test_auto_restart_paused_sends_down_alert(fake_server, sent_emails):
    _start()
    app.state.runtime.crash_times = [time.monotonic()] * common_module.CRASH_RESTART_LIMIT
    fake_server[0].exit(1)
    assert _wait_until(lambda: any("DOWN" in subject for subject, _ in sent_emails))


def test_new_dashboard_link_is_emailed_once_per_link(fake_cloudflared, sent_emails):
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert _wait_until(lambda: any("New dashboard link" in s for s, _ in sent_emails))
    link_mail = next(body for subject, body in sent_emails if "New dashboard link" in subject)
    assert "https://demo-1.trycloudflare.com" in link_mail

    fake_cloudflared[0].crash()  # tunnel drops and comes back with a new link
    assert _wait_until(lambda: sum("New dashboard link" in s for s, _ in sent_emails) == 2)
    assert any("went down" in subject for subject, _ in sent_emails)
    assert notifier.load_config()["last_link"] == "https://demo-2.trycloudflare.com"


def test_alert_settings_never_return_the_password():
    notifier.save_config({"smtp_user": "bot@gmail.com", "smtp_password": "secret-app-pass"})
    response = client.get("/api/notifications", auth=("admin", "s3cret-pass"))
    assert "secret-app-pass" not in response.text
    assert response.json()["password_set"] is True

    client.put("/api/notifications", auth=("admin", "s3cret-pass"),
               json={"email_to": "me@example.com", "smtp_password": ""})
    assert notifier.load_config()["smtp_password"] == "secret-app-pass"  # empty keeps the saved one


def test_alert_test_endpoint(monkeypatch):
    response = client.post("/api/notifications/test", auth=("admin", "s3cret-pass"))
    assert response.status_code == 400

    pinged = []
    monkeypatch.setattr(notifier, "ping_heartbeat", lambda url: pinged.append(url) or True)
    notifier.save_config({"heartbeat_url": "https://hc-ping.com/abc"})
    response = client.post("/api/notifications/test", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert pinged == ["https://hc-ping.com/abc"]


def test_playit_agent_crash_restarts_and_alerts(fake_playit, sent_emails, monkeypatch):
    monkeypatch.setattr(common_module, "PLAYIT_RETRY_SEC", 0)
    agents = []

    class TrackedAgent(DummyAgent):
        def __init__(self, on_output=None, on_exit=None):
            super().__init__(on_output, on_exit)
            self.on_exit = on_exit
            agents.append(self)

    monkeypatch.setattr(playit, "PlayitAgent", TrackedAgent)
    playit.save_config("demo-secret", "")
    client.post("/api/playit/start", auth=("admin", "s3cret-pass"))
    agents[0].running = False
    agents[0].on_exit(1)  # unexpected exit
    assert _wait_until(lambda: len(agents) == 2 and agents[1].is_running())
    assert any("PlayIt tunnel stopped unexpectedly" in subject for subject, _ in sent_emails)

    client.post("/api/playit/stop", auth=("admin", "s3cret-pass"))  # deliberate: no restart
    agents[1].on_exit(1)
    time.sleep(0.2)
    assert len(agents) == 2


def test_playit_rejected_key_stops_agent_without_restart(fake_playit, sent_emails, monkeypatch):
    monkeypatch.setattr(common_module, "PLAYIT_RETRY_SEC", 0)
    monkeypatch.setattr(playit.time, "sleep", lambda seconds: None)
    agents = []

    class TalkingAgent(DummyAgent):
        def __init__(self, on_output=None, on_exit=None):
            super().__init__(on_output, on_exit)
            self.on_output, self.on_exit = on_output, on_exit
            agents.append(self)

    def fake_api(path, body, secret=None):
        if path == "/agents/rundata":
            return {"data": {"agent_id": "agent-1"}}
        if path == "/tunnels/list":
            return {"data": {"tunnels": []}}
        # The agent can't register with a rejected key, so the API keeps refusing to create the tunnel.
        agents[0].on_output("ERROR playit_agent_core::agent_control: failed to sign and register "
                            "error=ApiError(Auth(InvalidAgentKey))")
        raise RuntimeError("playit API /tunnels/create: AgentVersionTooOld")

    monkeypatch.setattr(playit, "PlayitAgent", TalkingAgent)
    monkeypatch.setattr(playit, "_api", fake_api)
    monkeypatch.setattr(playit, "ensure_tunnel",
                        lambda secret, port, log, timeout_sec=120, should_abort=None: playit._ensure_tunnel(
                            secret, port, log, timeout_sec, playit._is_mc_tunnel, "EZ", "minecraft-java", "Minecraft Java",
                            should_abort))
    playit.save_config("ab12cd34ef", "")
    auth = ("admin", "s3cret-pass")

    response = client.post("/api/playit/start", auth=auth)
    assert response.status_code == 502
    assert "InvalidAgentKey" in response.json()["detail"]
    assert not agents[0].is_running()
    status = client.get("/api/playit/status", auth=auth).json()
    assert status["setup"] == "error" and "Change account" in status["error"]
    assert any("PlayIt key rejected" in subject for subject, _ in sent_emails)

    agents[0].on_exit(1)  # the stopped agent exits: deliberate, so no restart
    threading.Event().wait(0.2)  # time.sleep is stubbed above
    assert len(agents) == 1


def test_playit_agent_restarts_pause_after_repeated_exits(fake_playit, sent_emails, monkeypatch):
    monkeypatch.setattr(common_module, "PLAYIT_RETRY_SEC", 0)
    events = []
    agents = []

    class FailingAgent(DummyAgent):
        def __init__(self, on_output=None, on_exit=None):
            super().__init__(on_output, on_exit)
            self.on_output, self.on_exit = on_output, on_exit
            agents.append(self)

    monkeypatch.setattr(playit, "kill_stale_agents", lambda log=print: events.append("kill") or 0)
    monkeypatch.setattr(playit, "PlayitAgent", FailingAgent)
    playit.save_config("ab12cd34ef", "")
    auth = ("admin", "s3cret-pass")
    client.post("/api/playit/start", auth=auth)
    assert events == ["kill"]  # leftovers are cleared before the first agent starts

    for attempt in range(common_module.PLAYIT_CRASH_LIMIT + 1):
        agent = agents[-1]
        agent.on_output("playitd error: IPC error: Another instance is already running")
        agent.running = False
        agent.on_exit(1)
        if attempt < common_module.PLAYIT_CRASH_LIMIT:
            assert _wait_until(lambda: len(agents) == attempt + 2 and agents[-1].is_running())

    threading.Event().wait(0.2)
    assert len(agents) == common_module.PLAYIT_CRASH_LIMIT + 1  # no restart after the limit
    status = client.get("/api/playit/status", auth=auth).json()
    assert status["setup"] == "error"
    assert "Another instance is already running" in status["error"]
    assert any("auto-restart paused" in subject for subject, _ in sent_emails)

    assert client.post("/api/playit/start", auth=auth).json()["state"] == "running"  # manual start re-arms
    assert client.get("/api/playit/status", auth=auth).json()["setup"] == "idle"


def test_playit_secret_validation_and_ansi_stripping():
    assert playit.is_valid_secret(" ab12CD34ef\n")
    assert not playit.is_valid_secret("pasted-key")
    assert not playit.is_valid_secret("")
    assert playit.is_key_rejected("failed to authenticate error=ApiError(Auth(InvalidAgentKey))")
    assert not playit.is_key_rejected("[playit] Waiting for the playit agent to register...")
    line = "\x1b[2m2026-10-22T14:06:59Z\x1b[0m \x1b[31mERROR\x1b[0m failed \x1b[3merror\x1b[0m\x1b[2m=\x1b[0mx"
    assert playit._ANSI_ESCAPE.sub("", line) == "2026-10-22T14:06:59Z ERROR failed error=x"


def test_remote_dashboard_is_on_by_default():
    assert common_module._default_config()["remote_dashboard"] is True
    text = client.get("/dashboard/setup").text
    for element_id in ("alerts-form", "alerts-email-enabled", "alerts-heartbeat-url", "alerts-test-button"):
        assert f'id="{element_id}"' in text


def test_webhook_urls_are_write_only_and_validated():
    response = _hooks()
    assert response.status_code == 200
    body = response.json()
    assert body["discord_owner_set"] is True and body["discord_friends_set"] is True
    everything = json.dumps(client.get("/api/notifications", auth=AUTH).json())
    assert "owner-token" not in everything and "friends-token" not in everything
    assert client.put("/api/notifications", auth=AUTH, json={"discord_owner_url": ""}).json()["discord_owner_set"] is True
    assert client.put("/api/notifications", auth=AUTH, json={"discord_owner_url": None}).json()["discord_owner_set"] is False
    bad = client.put("/api/notifications", auth=AUTH, json={"discord_friends_url": "https://evil.example/api/webhooks/1/x"})
    assert bad.status_code == 400 and "Discord webhook" in bad.json()["detail"]


def test_owner_alerts_go_to_owner_webhook_only(discord_posts):
    _hooks()
    app.state.notify("link", "Owner-only test alert", "https://secret-link.trycloudflare.com")
    assert _wait_until(lambda: len(discord_posts) == 1)
    url, content = discord_posts[0]
    assert url == OWNER_HOOK and "Owner-only test alert" in content
    time.sleep(0.1)
    assert all(url != FRIENDS_HOOK for url, _ in discord_posts)


def test_friends_see_online_offline_and_joins_but_never_the_dashboard_link(fake_server, fake_cloudflared,
                                                                          discord_posts, offline_network):
    client.put("/api/notifications", auth=AUTH, json={"discord_friends_url": FRIENDS_HOOK})  # friends hook only
    playit.save_config("k", "friends.joinmc.link")
    _start()
    assert _wait_until(lambda: any("Server is online" in c for _, c in discord_posts))
    online = next(c for _, c in discord_posts if "online" in c)
    assert "Vanilla 1.21.7 at friends.joinmc.link" in online
    services = app.state.services
    services.player_event("Alex", "joined")
    services.player_event("Sam", "joined")
    services.player_event("Bob", "left")
    assert _wait_until(lambda: any("Alex, Sam joined · Bob left" in c for _, c in discord_posts))
    client.post("/api/remote-dashboard", auth=AUTH, json={"enabled": True})
    assert _wait_until(lambda: app.state.runtime.web_tunnel_url is not None)
    client.post("/api/server/stop", auth=AUTH)
    fake_server[0].exit(0)
    assert _wait_until(lambda: any("Server is offline" in c for _, c in discord_posts))
    time.sleep(0.1)
    assert all(url == FRIENDS_HOOK for url, _ in discord_posts)
    assert not any("trycloudflare" in content for _, content in discord_posts)


def test_friends_get_the_new_status_page_link(fake_cloudflared, discord_posts, offline_network):
    client.put("/api/notifications", auth=AUTH, json={"discord_friends_url": FRIENDS_HOOK})
    _, token = _enable_status_page()
    client.post("/api/remote-dashboard", auth=AUTH, json={"enabled": True})
    assert _wait_until(lambda: any(f"/s/{token}" in c for _, c in discord_posts))
    [content] = [c for _, c in discord_posts if "/s/" in c]
    assert content.endswith(f"https://demo-1.trycloudflare.com/s/{token}")


def test_join_then_leave_within_the_batch_says_nothing(discord_posts):
    client.put("/api/notifications", auth=AUTH, json={"discord_friends_url": FRIENDS_HOOK})
    app.state.services.player_event("Ghost", "joined")
    app.state.services.player_event("Ghost", "left")
    time.sleep(0.2)
    assert discord_posts == []


def test_friend_switches_turn_events_off(fake_server, discord_posts):
    client.put("/api/notifications", auth=AUTH, json={"discord_friends_url": FRIENDS_HOOK,
                                                      "friends_notify_status": False})
    _start()
    time.sleep(0.2)
    assert discord_posts == []


def test_send_discord_waits_once_on_rate_limit(monkeypatch):
    calls = []

    class Reply:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            return {"retry_after": 0.01}

    replies = [Reply(429), Reply(204)]
    monkeypatch.setattr(notifier.requests, "post", lambda url, json, timeout: calls.append(json) or replies.pop(0))
    notifier.send_discord(FRIENDS_HOOK, "@everyone hi")
    assert len(calls) == 2 and calls[0]["allowed_mentions"] == {"parse": []}


def test_test_button_posts_to_each_webhook(discord_posts):
    _hooks()
    response = client.post("/api/notifications/test", auth=AUTH)
    assert response.status_code == 200 and "Discord owner channel OK" in response.json()["message"]
    assert [url for url, _ in discord_posts] == [OWNER_HOOK, FRIENDS_HOOK]
