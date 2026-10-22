import importlib
import os
import re
import threading
import time
import zipfile

os.environ["EZ_WEBAPP_USERNAME"] = "admin"
os.environ["EZ_WEBAPP_PASSWORD"] = "s3cret-pass"

import pytest
from fastapi.testclient import TestClient

from core import config_store, paths, playit
from webapp.app import create_app

webapp_module = importlib.import_module("webapp.app")

app = create_app()
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep tests away from the real EZ_Server_Data (launcher config, PlayIt secret, servers)."""
    data_dir = tmp_path / "EZ_Server_Data"
    monkeypatch.setattr(paths, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(paths, "JAVA_DIR", str(data_dir / "java"))
    monkeypatch.setattr(paths, "SERVERS_DIR", str(data_dir / "servers"))
    monkeypatch.setattr(config_store, "_FILE", str(data_dir / "launcher_config.json"))
    monkeypatch.setattr(playit, "_CONFIG_FILE", str(data_dir / "playit_config.json"))


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_serves_html():
    response = client.get("/", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "EZ Minecraft Server" in response.text
    assert "Dashboard" in response.text


def test_remote_page_mentions_playit_support():
    response = client.get("/dashboard/remote")
    assert response.status_code == 200
    assert "PlayIt" in response.text


def test_server_controls_are_on_every_page():
    for section in ("overview", "setup", "advanced", "mods", "remote", "backups", "console"):
        text = client.get(f"/dashboard/{section}").text
        for control in ('id="start-button"', 'id="stop-button"', 'id="restart-button"', 'id="server-state"'):
            assert control in text, (section, control)


def test_management_features_live_on_their_pages():
    expectations = {
        "overview": ('id="backup-button"', 'id="metrics"', 'id="console-output"'),
        "mods": ('id="mod-search-form"', 'id="mod-upload-input"', 'id="mod-installed-list"'),
        "backups": ('id="backup-button"', 'id="backup-list"'),
        "console": ('id="console-output"', 'id="command-form"', 'id="admin-op"', 'id="admin-save"'),
        "remote": ('id="playit-config-button"', 'id="playit-start-button"', 'id="playit-stop-button"'),
    }
    for section, ids in expectations.items():
        text = client.get(f"/dashboard/{section}").text
        for element_id in ids:
            assert element_id in text, (section, element_id)


def test_dashboard_sections_have_their_own_pages():
    for section in ("overview", "setup", "advanced", "mods", "remote", "backups", "console"):
        response = client.get(f"/dashboard/{section}", auth=("admin", "s3cret-pass"))
        assert response.status_code == 200
        assert "EZ Minecraft" in response.text


def test_dashboard_does_not_embed_credentials():
    response = client.get("/")
    assert response.status_code == 200
    assert 'type="password"' not in response.text
    assert 'value="admin"' not in response.text


def test_every_dashboard_page_has_action_status_bar():
    for section in ("overview", "setup", "advanced", "mods", "remote", "backups", "console"):
        response = client.get(f"/dashboard/{section}")
        assert 'id="action-status"' in response.text


def test_advanced_page_has_save_control():
    response = client.get("/dashboard/advanced")
    assert 'id="advanced-save-button"' in response.text


def test_dashboard_js_only_calls_existing_api_routes():
    js_path = os.path.join(os.path.dirname(webapp_module.__file__), "static", "js", "dashboard.js")
    with open(js_path, "r", encoding="utf-8") as handle:
        source = handle.read()
    route_paths = {route.path for route in app.routes}
    called = set(re.findall(r"['`](/api/[^'`?]*)", source))
    assert called, "expected dashboard.js to call the API"
    for url in called:
        if "${" in url:
            prefix = url.split("${", 1)[0]
            assert any(path.startswith(prefix) and "{" in path for path in route_paths), url
        else:
            assert url in route_paths, url


def test_console_history_endpoint_returns_messages():
    app.state.console_manager.publish("hello from history endpoint")
    response = client.get("/api/console", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "lines" in payload
    assert "hello from history endpoint" in payload["lines"]


def test_status_requires_authentication():
    response = client.get("/api/status")
    assert response.status_code == 401


def test_status_endpoint_returns_state_when_authenticated():
    response = client.get("/api/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "state" in payload
    assert "loader" in payload
    assert "version" in payload
    assert "lan_address" in payload
    assert "public_address" in payload
    assert "playit" in payload


def test_modrinth_install_accepts_project_id(monkeypatch):
    captured = {}

    def fake_install(identifier, loader, version, target_folder, log=print):
        captured["identifier"] = identifier
        captured["loader"] = loader
        captured["version"] = version
        captured["target_folder"] = target_folder
        return "fake-mod.jar"

    monkeypatch.setattr(webapp_module.modrinth, "install", fake_install)
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {"loader": "fabric", "version": "1.21.7"})

    response = client.post(
        "/api/modrinth/install",
        auth=("admin", "s3cret-pass"),
        json={"project_id": "abc123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "fake-mod.jar"
    assert captured["identifier"] == "abc123"


def test_legacy_backup_and_mod_routes_still_work():
    response = client.get("/api/backups", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "backups" in response.json()

    response = client.get("/api/mods", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "mods" in response.json()


def test_admin_action_routes_exist_for_dashboard_buttons():
    for action in ("op", "deop", "kick", "ban", "day", "night", "clear", "rain", "save"):
        response = client.post(f"/api/admin/{action}", auth=("admin", "s3cret-pass"), json={"player": "Steve"})
        assert response.status_code in {200, 409, 400}


def test_playit_status_endpoint_reports_configuration():
    response = client.get("/api/playit/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "configured" in payload
    assert "state" in payload
    assert "address" in payload


def test_system_metrics_endpoint_returns_cpu_and_ram():
    response = client.get("/api/system/metrics", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "cpu" in payload
    assert "ram" in payload
    assert "ramTotal" in payload
    assert 0 <= payload["cpu"] <= 100
    assert payload["ram"] >= 0
    assert payload["ramTotal"] > 0


class DummyAgent:
    def __init__(self, on_output=None, on_exit=None):
        self.running = False

    def is_running(self):
        return self.running

    def start(self, secret):
        self.secret = secret
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def fake_playit(monkeypatch):
    """Stub the PlayIt network/agent calls."""
    monkeypatch.setattr(webapp_module.playit, "ensure_agent", lambda log=print: "agent")
    monkeypatch.setattr(webapp_module.playit, "PlayitAgent", DummyAgent)
    monkeypatch.setattr(webapp_module.playit, "ensure_tunnel", lambda secret, port, log, timeout_sec=120: "demo.example:25565")
    app.state.runtime.playit_agent = None
    app.state.runtime.playit_setup = "idle"
    yield
    app.state.runtime.playit_agent = None
    app.state.runtime.playit_setup = "idle"


def test_playit_start_route_starts_the_tunnel(fake_playit):
    playit.save_config("demo-secret", "")
    response = client.post("/api/playit/start", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "running"
    assert payload["address"] == "demo.example:25565"


def test_playit_configure_returns_approval_link_and_finishes_in_background(fake_playit, monkeypatch):
    approved = threading.Event()

    def fake_claim(log, should_cancel, timeout_sec=300, on_url=None):
        on_url("https://playit.gg/claim/abc123")
        approved.wait(5)
        playit.save_config("new-secret", "")
        return "new-secret"

    monkeypatch.setattr(webapp_module.playit, "claim_interactive", fake_claim)
    response = client.post("/api/playit/configure", auth=("admin", "s3cret-pass"))
    payload = response.json()
    assert payload["setup"] == "awaiting-approval"
    assert payload["claim_url"] == "https://playit.gg/claim/abc123"

    approved.set()
    deadline = time.monotonic() + 5
    while app.state.runtime.playit_setup != "idle" and time.monotonic() < deadline:
        time.sleep(0.05)
    status = client.get("/api/playit/status", auth=("admin", "s3cret-pass")).json()
    assert status["setup"] == "idle"
    assert status["state"] == "running"
    assert status["address"] == "demo.example:25565"
    assert status["claim_url"] == ""


def test_playit_configure_reset_forgets_saved_account(fake_playit, monkeypatch):
    playit.save_config("old-secret", "old.example:1")
    monkeypatch.setattr(webapp_module.playit, "claim_interactive",
                        lambda log, should_cancel, timeout_sec=300, on_url=None: on_url("https://playit.gg/claim/new") or threading.Event().wait())
    response = client.post("/api/playit/configure", auth=("admin", "s3cret-pass"), json={"reset": True})
    payload = response.json()
    assert payload["configured"] is False
    assert payload["claim_url"] == "https://playit.gg/claim/new"
    assert "secret" not in playit.load_config()
    app.state.runtime.playit_cancel = True


class FakeQuickTunnel:
    """Stand-in for cloudflared.QuickTunnel; the test decides when the URL arrives or it exits."""

    instances: list["FakeQuickTunnel"] = []

    def __init__(self, on_url, on_output, on_exit):
        self.on_url = on_url
        self.on_exit = on_exit
        self.running = False
        self.port = None
        FakeQuickTunnel.instances.append(self)

    def start(self, binary, local_port):
        self.running = True
        self.port = local_port
        self.on_url(f"https://demo-{len(FakeQuickTunnel.instances)}.trycloudflare.com")

    def is_running(self):
        return self.running

    def stop(self):
        self.running = False
        self.on_exit(0)

    def crash(self):
        self.running = False
        self.on_exit(1)


@pytest.fixture
def fake_cloudflared(monkeypatch):
    FakeQuickTunnel.instances = []
    monkeypatch.setattr(webapp_module.cloudflared, "ensure_binary", lambda log=print: "cloudflared")
    monkeypatch.setattr(webapp_module.cloudflared, "QuickTunnel", FakeQuickTunnel)
    monkeypatch.setattr(webapp_module, "WEB_TUNNEL_RETRY_SEC", 0)
    yield FakeQuickTunnel.instances
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": False})


def test_remote_dashboard_toggle(fake_cloudflared):
    response = client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert response.json()["enabled"] is True
    assert _wait_until(lambda: client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()["state"] == "running")
    status = client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()
    assert status["url"] == "https://demo-1.trycloudflare.com"
    assert fake_cloudflared[0].port == webapp_module.WEB_PORT

    response = client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": False})
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["state"] == "off"
    assert payload["url"] == ""
    assert not fake_cloudflared[0].is_running()


def test_remote_dashboard_restarts_after_unexpected_exit(fake_cloudflared):
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert _wait_until(lambda: len(fake_cloudflared) == 1 and fake_cloudflared[0].is_running())
    fake_cloudflared[0].crash()
    assert _wait_until(lambda: len(fake_cloudflared) == 2 and app.state.runtime.web_tunnel_state == "running")
    assert app.state.runtime.web_tunnel_url == "https://demo-2.trycloudflare.com"


def test_remote_dashboard_reports_missing_binary(monkeypatch):
    def missing(log=print):
        raise RuntimeError("cloudflared is not installed. Install it in Termux with: pkg install cloudflared")

    monkeypatch.setattr(webapp_module.cloudflared, "ensure_binary", missing)
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert _wait_until(lambda: app.state.runtime.web_tunnel_state == "error")
    status = client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()
    assert "pkg install cloudflared" in status["error"]
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": False})


def test_quick_tunnel_announces_url_only_after_registration():
    from core import cloudflared

    lines = [
        "INF Requesting new quick Tunnel on trycloudflare.com...\n",
        "INF |  https://quiet-river-boat-lamp.trycloudflare.com  |\n",
        "INF Starting metrics server\n",
        "INF Registered tunnel connection connIndex=0 location=lis01\n",
    ]
    events = []

    class FakeProc:
        stdout = iter(lines)

        def wait(self):
            return 0

    tunnel = cloudflared.QuickTunnel(
        on_url=lambda url: events.append(("url", url)),
        on_output=lambda line: events.append(("line", line[:12])),
        on_exit=lambda code: events.append(("exit", code)),
    )
    tunnel.proc = FakeProc()
    tunnel._reader()
    url_index = events.index(("url", "https://quiet-river-boat-lamp.trycloudflare.com"))
    registered_index = events.index(("line", "INF Register"))
    assert url_index < registered_index  # announced while handling the registration line
    assert ("line", "INF Starting") in events[:url_index]  # not before registration
    assert events[-1] == ("exit", 0)


def test_cloudflared_url_parsing_and_assets(monkeypatch):
    from core import cloudflared

    line = "2026-10-22T01:00:00Z INF |  https://quiet-river-boat-lamp.trycloudflare.com  |"
    assert cloudflared.find_url(line) == "https://quiet-river-boat-lamp.trycloudflare.com"
    assert cloudflared.find_url("INF Starting tunnel") is None
    monkeypatch.setattr(cloudflared.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cloudflared.platform, "machine", lambda: "AMD64")
    assert cloudflared._asset_name() == "cloudflared-windows-amd64.exe"
    monkeypatch.setattr(cloudflared.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cloudflared.platform, "machine", lambda: "aarch64")
    assert cloudflared._asset_name() == "cloudflared-linux-arm64"


def test_system_java_prefers_exact_then_oldest_newer(monkeypatch):
    from core import java_manager

    versions = {"/jvm/17/bin/java": 17, "/jvm/21/bin/java": 21, "/jvm/25/bin/java": 25}
    monkeypatch.setattr(java_manager, "_system_java_candidates", lambda: list(versions))
    monkeypatch.setattr(java_manager, "java_major", lambda exe: versions[exe])
    assert java_manager.find_system_java(21) == ("/jvm/21/bin/java", 21)
    assert java_manager.find_system_java(8) == ("/jvm/17/bin/java", 17)
    assert java_manager.find_system_java(26) == (None, None)


def test_missing_java_on_termux_explains_how_to_install(monkeypatch):
    from core import hostenv, java_manager

    monkeypatch.setattr(hostenv, "is_windows", lambda: False)
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(java_manager, "_system_java_candidates", lambda: [])
    with pytest.raises(RuntimeError, match="pkg install openjdk-21"):
        java_manager.ensure_java(21, log=lambda message: None)


def test_forge_args_file_matches_platform(tmp_path, monkeypatch):
    from core import downloaders

    args_dir = tmp_path / "libraries" / "net" / "forge"
    args_dir.mkdir(parents=True)
    (args_dir / "unix_args.txt").write_text("")
    monkeypatch.setattr(downloaders.sys, "platform", "linux")
    assert downloaders._find_args_file(str(tmp_path)).endswith("unix_args.txt")
    monkeypatch.setattr(downloaders.sys, "platform", "win32")
    assert downloaders._find_args_file(str(tmp_path)) is None


def test_metrics_survive_blocked_cpu_counters(monkeypatch):
    class BlockedPsutil:
        @staticmethod
        def cpu_percent(interval=None):
            raise PermissionError("/proc/stat")

        @staticmethod
        def virtual_memory():
            class Mem:
                used = 1024
                total = 4096
            return Mem()

    monkeypatch.setattr(webapp_module, "psutil", BlockedPsutil)
    snapshot = webapp_module._system_metrics_snapshot()
    assert snapshot["cpu"] == 0.0
    assert snapshot["ram"] == 1024
    assert snapshot["ramTotal"] == 4096


def test_playit_agent_uses_termux_chroot_on_termux(monkeypatch):
    monkeypatch.setattr(playit.hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(playit.shutil, "which", lambda name: "/usr/bin/termux-chroot")
    monkeypatch.setattr(playit, "_agent_path", lambda: "/data/playit-linux-aarch64")
    assert playit.agent_command("s")[:2] == ["/usr/bin/termux-chroot", "/data/playit-linux-aarch64"]
    monkeypatch.setenv("EZ_PLAYIT_CHROOT", "0")
    assert playit.agent_command("s")[0] == "/data/playit-linux-aarch64"


def test_playit_agent_name_is_platform_aware(monkeypatch):
    monkeypatch.setattr(webapp_module.playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(webapp_module.playit.platform, "machine", lambda: "aarch64")
    assert webapp_module.playit._agent_binary_name() == "playit-linux-aarch64"

    monkeypatch.setattr(webapp_module.playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(webapp_module.playit.platform, "machine", lambda: "x86_64")
    assert webapp_module.playit._agent_binary_name() == "playit-linux-amd64"

    monkeypatch.setattr(webapp_module.playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(webapp_module.playit.platform, "machine", lambda: "armv7l")
    assert webapp_module.playit._agent_binary_name() == "playit-linux-armv7"


def test_config_can_persist_values():
    response = client.put(
        "/api/config",
        auth=("admin", "s3cret-pass"),
        json={"loader": "vanilla", "version": "1.21.7", "ram_mb": 2048},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["loader"] == "vanilla"
    assert payload["version"] == "1.21.7"
    assert payload["ram_mb"] == 2048


def test_versions_endpoint_accepts_known_loader():
    response = client.get("/api/versions/vanilla", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "loader" in data
    assert "versions" in data
    assert data["loader"] == "vanilla"


def test_websocket_console_requires_authentication():
    try:
        with client.websocket_connect("/ws/console"):
            pass
    except Exception:
        return
    raise AssertionError("Unauthenticated WebSocket access should be rejected.")


def test_websocket_console_streams_messages():
    app.state.console_manager.history.clear()
    with client.websocket_connect(
        "/ws/console",
        headers={"Authorization": "Basic YWRtaW46czNjcmV0LXBhc3M="},
    ) as websocket:
        app.state.console_manager.publish("hello from websocket")
        message = websocket.receive_text()
        assert message == "hello from websocket"


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

    monkeypatch.setattr(webapp_module, "_server_is_installed", lambda target_dir, loader: False)
    monkeypatch.setattr(webapp_module.downloaders, "prepare", fake_prepare)
    monkeypatch.setattr(webapp_module.java_manager, "ensure_java", lambda required: "java")
    monkeypatch.setattr(webapp_module.downloaders, "launch_args", lambda *args, **kwargs: ["java", "-jar", "server.jar"])
    monkeypatch.setattr(webapp_module, "ServerProcess", DummyProcess)
    monkeypatch.setattr(webapp_module, "update_properties", fake_update_properties)

    response = client.post(
        "/api/server/start",
        auth=("admin", "s3cret-pass"),
        json={"server_port": 25566, "motd": "Advanced test", "max_players": 12},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Server started."
    assert app.state.runtime.install_status == "ready"
    assert app.state.runtime.install_progress >= 0
    assert calls["kwargs"]["server-port"] == 25566
    assert calls["kwargs"]["motd"] == "Advanced test"
    assert calls["kwargs"]["max-players"] == 12


def test_server_lifecycle_start_and_stop():
    start_response = client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    assert start_response.status_code == 200
    payload = start_response.json()
    assert payload["state"] in {"starting", "running"}

    command_response = client.post(
        "/api/server/command",
        auth=("admin", "s3cret-pass"),
        json={"command": "say hello"},
    )
    assert command_response.status_code == 200

    stop_response = client.post("/api/server/stop", auth=("admin", "s3cret-pass"))
    assert stop_response.status_code == 200


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

    monkeypatch.setattr(webapp_module, "_server_is_installed", lambda target_dir, loader: True)
    monkeypatch.setattr(webapp_module.java_manager, "ensure_java", lambda required: "java")
    monkeypatch.setattr(webapp_module.downloaders, "launch_args", lambda *args, **kwargs: ["java"])
    monkeypatch.setattr(webapp_module, "update_properties", lambda server_dir, **kwargs: None)
    monkeypatch.setattr(webapp_module, "ServerProcess", StoppableProcess)

    previous = StoppableProcess()
    previous.start()
    app.state.runtime.process = previous
    events.clear()

    response = client.post("/api/server/restart", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert response.json()["message"] == "Server started."
    assert events == ["stop", "start"]
    assert app.state.runtime.process is not previous


class RecordingProcess:
    """Stand-in for ServerProcess that the test can make exit with any code."""

    instances: list["RecordingProcess"] = []

    def __init__(self, args, cwd, on_output, on_exit):
        self.on_exit = on_exit
        self.running = False
        self.sent: list[str] = []
        RecordingProcess.instances.append(self)

    def start(self):
        self.running = True

    def is_running(self):
        return self.running

    def send(self, command):
        self.sent.append(command)

    def stop(self):
        self.running = False

    def kill(self):
        self.running = False

    def exit(self, code):
        self.running = False
        self.on_exit(code)


@pytest.fixture
def fake_server(monkeypatch):
    RecordingProcess.instances = []
    monkeypatch.setattr(webapp_module, "_server_is_installed", lambda target_dir, loader: True)
    monkeypatch.setattr(webapp_module.java_manager, "ensure_java", lambda required: "java")
    monkeypatch.setattr(webapp_module.downloaders, "launch_args", lambda *args, **kwargs: ["java"])
    monkeypatch.setattr(webapp_module, "update_properties", lambda server_dir, **kwargs: None)
    monkeypatch.setattr(webapp_module, "ServerProcess", RecordingProcess)
    monkeypatch.setattr(webapp_module, "CRASH_RESTART_DELAY_SEC", 0)
    runtime = app.state.runtime
    runtime.process = None
    runtime.crash_times = []
    yield RecordingProcess.instances
    runtime.process = None
    runtime.crash_times = []


def _wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


def test_crash_triggers_automatic_restart(fake_server):
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    fake_server[0].exit(1)
    assert _wait_until(lambda: len(fake_server) == 2)
    assert app.state.runtime.process is fake_server[1]
    assert app.state.runtime.state == "running"


def test_crash_restart_pauses_after_limit(fake_server):
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    now = time.monotonic()
    app.state.runtime.crash_times = [now] * webapp_module.CRASH_RESTART_LIMIT
    fake_server[0].exit(1)
    time.sleep(0.2)
    assert len(fake_server) == 1
    assert app.state.runtime.state == "crashed"
    history = client.get("/api/console", auth=("admin", "s3cret-pass")).json()["lines"]
    assert any("automatic restart paused" in line for line in history)


def test_deliberate_stops_are_not_crashes(fake_server):
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    client.post("/api/server/stop", auth=("admin", "s3cret-pass"))
    fake_server[0].exit(1)

    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    client.post("/api/server/command", auth=("admin", "s3cret-pass"), json={"command": "/stop"})
    fake_server[1].exit(143)
    time.sleep(0.2)
    assert len(fake_server) == 2
    assert app.state.runtime.state == "stopped"


def test_auto_restart_can_be_disabled(fake_server, monkeypatch):
    original = webapp_module._effective_config
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {**original(), "auto_restart": False})
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    fake_server[0].exit(1)
    time.sleep(0.2)
    assert len(fake_server) == 1


def test_autostart_launches_server_and_playit(fake_server, fake_playit, monkeypatch):
    original = webapp_module._effective_config
    monkeypatch.setattr(webapp_module, "_effective_config",
                        lambda: {**original(), "autostart_server": True, "autostart_playit": True})
    monkeypatch.delenv("EZ_NO_AUTOSTART", raising=False)
    playit.save_config("demo-secret", "")
    app.state.autostart()
    assert _wait_until(lambda: len(fake_server) == 1 and app.state.runtime.playit_agent is not None)
    assert _wait_until(lambda: playit.load_config().get("address") == "demo.example:25565")


def test_autostart_respects_kill_switch(fake_server, monkeypatch):
    original = webapp_module._effective_config
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {**original(), "autostart_server": True})
    monkeypatch.setenv("EZ_NO_AUTOSTART", "1")
    app.state.autostart()
    time.sleep(0.2)
    assert fake_server == []


def test_shutdown_stops_server_and_tunnel(fake_server, fake_playit):
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    agent = DummyAgent()
    agent.start("s")
    app.state.runtime.playit_agent = agent
    app.state.shutdown()
    assert not fake_server[0].is_running()
    assert not agent.is_running()
    assert app.state.runtime.stop_requested is True


def test_always_on_settings_render_on_setup_page():
    text = client.get("/dashboard/setup").text
    for element_id in ("autostart-server", "auto-restart", "autostart-playit", "always-on-save-button"):
        assert f'id="{element_id}"' in text


def test_mod_upload_rejected_for_vanilla(monkeypatch):
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {"loader": "vanilla", "version": "1.21.7"})
    response = client.post(
        "/api/server/mods/upload",
        auth=("admin", "s3cret-pass"),
        files={"files": ("demo.jar", b"jar-bytes", "application/java-archive")},
    )
    assert response.status_code == 400
    assert "vanilla" in response.json()["detail"]


def test_player_tracking_from_console_lines():
    runtime = webapp_module.RuntimeState()
    runtime.track_player_event("[12:00:00] [Server thread/INFO]: Steve joined the game")
    runtime.track_player_event("[12:00:01] [Server thread/INFO]: Alex joined the game")
    runtime.track_player_event("[12:00:02] [Server thread/INFO]: <Steve> Bob joined the game")
    runtime.track_player_event("[12:00:03] [Server thread/INFO]: Alex left the game")
    snapshot = runtime.snapshot()
    assert snapshot["players"] == 1
    assert snapshot["player_names"] == ["Steve"]


def _make_backup(server_dir, name, members):
    path = os.path.join(server_dir, name)
    with zipfile.ZipFile(path, "w") as zf:
        for member in members:
            zf.writestr(member, "data")
    return path


def test_backup_restore_round_trip_and_validation(monkeypatch):
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {"loader": "vanilla", "version": "1.21.7"})
    app.state.runtime.process = None
    server_dir = paths.server_dir("vanilla", "1.21.7")
    good = _make_backup(server_dir, "world-backup-20260101-000000.zip", ["world/level.dat"])
    _make_backup(server_dir, "world-backup-evil.zip", ["world/../../escape.txt"])

    listed = client.get("/api/server/backups", auth=("admin", "s3cret-pass")).json()["backups"]
    assert good in listed

    response = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"), json={"backup_path": good})
    assert response.status_code == 200
    assert os.path.isfile(os.path.join(server_dir, "world", "level.dat"))

    response = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"),
                           json={"backup_path": "world-backup-evil.zip"})
    assert response.status_code == 400
    assert not os.path.exists(os.path.join(paths.DATA_DIR, "escape.txt"))

    outside = _make_backup(paths.DATA_DIR, "world-backup-outside.zip", ["world/level.dat"])
    response = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"), json={"backup_path": outside})
    assert response.status_code == 400


def test_playit_config_does_not_expose_secret(monkeypatch):
    monkeypatch.setattr(webapp_module.playit, "load_config", lambda: {"secret": "top-secret", "address": "demo.example"})
    response = client.get("/api/playit/config", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "top-secret" not in response.text
    assert response.json() == {"address": "demo.example", "secret_present": True}


def test_wrong_password_is_rejected():
    response = client.get("/api/status", auth=("admin", "wrong"))
    assert response.status_code == 401


def test_playit_status_is_not_running_without_agent(monkeypatch):
    monkeypatch.setattr(webapp_module.playit, "load_config", lambda: {"secret": "demo-secret", "address": "demo.example"})
    app.state.runtime.playit_agent = None
    response = client.get("/api/playit/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert response.json()["state"] == "configured"
