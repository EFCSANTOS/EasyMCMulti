import importlib
import json
import os
import re
import threading
import time
import zipfile

os.environ["EZ_WEBAPP_USERNAME"] = "admin"
os.environ["EZ_WEBAPP_PASSWORD"] = "s3cret-pass"

import pytest
from fastapi.testclient import TestClient

from core import config_store, notifier, paths, playit
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
    monkeypatch.setattr(notifier, "_CONFIG_FILE", str(data_dir / "notify_config.json"))


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
    monkeypatch.setattr(webapp_module.playit, "ensure_tunnel", lambda secret, port, log, timeout_sec=120, should_abort=None: "demo.example:25565")
    monkeypatch.setattr(webapp_module.playit, "kill_stale_agents", lambda log=print: 0)
    app.state.runtime.playit_agent = None
    app.state.runtime.playit_setup = "idle"
    app.state.runtime.playit_crash_times = []
    yield
    if app.state.runtime.playit_restart_timer is not None:
        app.state.runtime.playit_restart_timer.cancel()
    app.state.runtime.playit_restart_timer = None
    app.state.runtime.playit_agent = None
    app.state.runtime.playit_setup = "idle"
    app.state.runtime.playit_error = ""
    app.state.runtime.playit_crash_times = []


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


def test_missing_java_on_termux_is_installed_automatically(monkeypatch):
    from core import hostenv, java_manager

    installed = {}
    monkeypatch.setattr(hostenv, "is_windows", lambda: False)
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(java_manager, "_system_java_candidates", lambda: list(installed))
    monkeypatch.setattr(java_manager, "java_major", lambda exe: installed[exe])

    def fake_install(package, log=print, timeout_sec=1800):
        installed[f"/jvm/{package}/bin/java"] = int(package.split("-")[1])

    monkeypatch.setattr(hostenv, "termux_install", fake_install)
    assert java_manager.ensure_java(25, log=lambda message: None) == "/jvm/openjdk-25/bin/java"
    assert java_manager.termux_java_package(18) == "openjdk-21"
    installed.clear()  # nothing installed: Java 8 has no Termux package
    with pytest.raises(RuntimeError, match="Choose Minecraft 1.17 or newer"):
        java_manager.ensure_java(8, log=lambda message: None)


def test_failed_java_install_on_termux_explains_how_to_install(monkeypatch):
    from core import hostenv, java_manager

    monkeypatch.setattr(hostenv, "is_windows", lambda: False)
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(java_manager, "_system_java_candidates", lambda: [])
    monkeypatch.setattr(hostenv, "_apt", lambda args, log, timeout_sec: (100, ["E: Unable to locate package openjdk-21"]))
    with pytest.raises(RuntimeError, match="pkg install openjdk-21"):
        java_manager.ensure_java(21, log=lambda message: None)


def test_termux_install_refreshes_package_lists_and_retries(monkeypatch):
    from core import hostenv

    calls = []

    def fake_apt(args, log, timeout_sec):
        calls.append(args)
        return (0 if calls.count(["install", "cloudflared"]) == 2 else 100), []

    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(hostenv, "_apt", fake_apt)
    hostenv.termux_install("cloudflared", log=lambda message: None)
    assert calls == [["install", "cloudflared"], ["update"], ["install", "cloudflared"]]


def test_cloudflared_is_installed_automatically_on_termux(monkeypatch):
    from core import cloudflared, hostenv

    installed = []
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(hostenv, "termux_install", lambda package, log=print: installed.append(package))
    monkeypatch.setattr(cloudflared.shutil, "which", lambda name: "/usr/bin/cloudflared" if installed else None)
    assert cloudflared.ensure_binary(log=lambda message: None) == "/usr/bin/cloudflared"
    assert installed == ["cloudflared"]


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
    monkeypatch.setattr(webapp_module.java_manager, "ensure_java", lambda required, log=print: "java")
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
    monkeypatch.setattr(webapp_module.java_manager, "ensure_java", lambda required, log=print: "java")
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
    monkeypatch.setattr(webapp_module.java_manager, "ensure_java", lambda required, log=print: "java")
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


def test_autostart_launches_server_and_playit(fake_server, fake_playit, fake_cloudflared, monkeypatch):
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
    app.state.runtime.shutting_down = False


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


def test_playit_config_put_saves_and_keeps_secret_when_blank(fake_playit):
    auth = ("admin", "s3cret-pass")
    assert client.put("/api/playit/config", auth=auth, json={"secret": "", "address": ""}).status_code == 400
    response = client.put("/api/playit/config", auth=auth, json={"secret": "  ab12cd34ef  ", "address": "a.example"})
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert "ab12cd34ef" not in response.text
    response = client.put("/api/playit/config", auth=auth, json={"secret": "", "address": "b.example"})
    assert response.status_code == 200
    assert playit.load_config() == {"secret": "ab12cd34ef", "address": "b.example"}
    assert client.put("/api/playit/config", auth=auth, json={"secret": "x", "extra": 1}).status_code == 400
    response = client.put("/api/playit/config", auth=auth, json={"secret": "not-a-playit-key"})
    assert response.status_code == 400
    assert "hexadecimal" in response.json()["detail"]
    assert playit.load_config()["secret"] == "ab12cd34ef"


def test_playit_config_put_cancels_pending_approval(fake_playit, monkeypatch):
    def fake_claim(log, should_cancel, timeout_sec=300, on_url=None):
        on_url("https://playit.gg/claim/abc123")
        while not should_cancel():
            time.sleep(0.01)
        raise RuntimeError("Setup cancelled.")

    monkeypatch.setattr(webapp_module.playit, "claim_interactive", fake_claim)
    auth = ("admin", "s3cret-pass")
    assert client.post("/api/playit/configure", auth=auth).json()["setup"] == "awaiting-approval"
    response = client.put("/api/playit/config", auth=auth, json={"secret": "ab12cd34ef"})
    assert response.status_code == 200
    assert response.json()["setup"] == "idle"
    time.sleep(0.1)
    status = client.get("/api/playit/status", auth=auth).json()
    assert status["setup"] == "idle" and status["error"] == ""
    assert client.post("/api/playit/start", auth=auth).json()["state"] == "running"


def test_wrong_password_is_rejected():
    response = client.get("/api/status", auth=("admin", "wrong"))
    assert response.status_code == 401


def test_playit_status_is_not_running_without_agent(monkeypatch):
    monkeypatch.setattr(webapp_module.playit, "load_config", lambda: {"secret": "demo-secret", "address": "demo.example"})
    app.state.runtime.playit_agent = None
    response = client.get("/api/playit/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert response.json()["state"] == "configured"


# ---------- Alerts ----------
@pytest.fixture
def sent_emails(monkeypatch):
    """Enable email alerts and capture what would be sent."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "send_email", lambda config, subject, body: sent.append((subject, body)))
    notifier.save_config({"email_enabled": True, "email_to": "me@example.com",
                          "smtp_user": "bot@gmail.com", "smtp_password": "abcd efgh"})
    yield sent


def test_crash_sends_email_and_repeats_are_throttled(fake_server, sent_emails):
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    fake_server[0].exit(1)
    assert _wait_until(lambda: any("crashed" in subject for subject, _ in sent_emails))
    assert _wait_until(lambda: any("back online" in subject for subject, _ in sent_emails))
    assert _wait_until(lambda: len(fake_server) == 2)
    fake_server[1].exit(1)  # same alert again within 10 minutes -> not emailed twice
    assert _wait_until(lambda: len(fake_server) == 3)
    time.sleep(0.2)
    assert sum("Minecraft server crashed" in subject for subject, _ in sent_emails) == 1


def test_auto_restart_paused_sends_down_alert(fake_server, sent_emails):
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    app.state.runtime.crash_times = [time.monotonic()] * webapp_module.CRASH_RESTART_LIMIT
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
    monkeypatch.setattr(webapp_module, "PLAYIT_RETRY_SEC", 0)
    agents = []

    class TrackedAgent(DummyAgent):
        def __init__(self, on_output=None, on_exit=None):
            super().__init__(on_output, on_exit)
            self.on_exit = on_exit
            agents.append(self)

    monkeypatch.setattr(webapp_module.playit, "PlayitAgent", TrackedAgent)
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
    monkeypatch.setattr(webapp_module, "PLAYIT_RETRY_SEC", 0)
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

    monkeypatch.setattr(webapp_module.playit, "PlayitAgent", TalkingAgent)
    monkeypatch.setattr(playit, "_api", fake_api)
    monkeypatch.setattr(webapp_module.playit, "ensure_tunnel",
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
    monkeypatch.setattr(webapp_module, "PLAYIT_RETRY_SEC", 0)
    events = []
    agents = []

    class FailingAgent(DummyAgent):
        def __init__(self, on_output=None, on_exit=None):
            super().__init__(on_output, on_exit)
            self.on_output, self.on_exit = on_output, on_exit
            agents.append(self)

    monkeypatch.setattr(webapp_module.playit, "kill_stale_agents", lambda log=print: events.append("kill") or 0)
    monkeypatch.setattr(webapp_module.playit, "PlayitAgent", FailingAgent)
    playit.save_config("ab12cd34ef", "")
    auth = ("admin", "s3cret-pass")
    client.post("/api/playit/start", auth=auth)
    assert events == ["kill"]  # leftovers are cleared before the first agent starts

    for attempt in range(webapp_module.PLAYIT_CRASH_LIMIT + 1):
        agent = agents[-1]
        agent.on_output("playitd error: IPC error: Another instance is already running")
        agent.running = False
        agent.on_exit(1)
        if attempt < webapp_module.PLAYIT_CRASH_LIMIT:
            assert _wait_until(lambda: len(agents) == attempt + 2 and agents[-1].is_running())

    threading.Event().wait(0.2)
    assert len(agents) == webapp_module.PLAYIT_CRASH_LIMIT + 1  # no restart after the limit
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
    assert webapp_module._default_config()["remote_dashboard"] is True
    text = client.get("/dashboard/setup").text
    for element_id in ("alerts-form", "alerts-email-enabled", "alerts-heartbeat-url", "alerts-test-button"):
        assert f'id="{element_id}"' in text


# ---------- Self-update ----------
def _git(cwd, *args):
    import subprocess

    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)


def _commit_file(repo, name, content, message):
    with open(os.path.join(repo, name), "w", encoding="utf-8") as handle:
        handle.write(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


@pytest.fixture
def git_repos(tmp_path, monkeypatch):
    """origin (bare) <- publisher clone pushes updates; app clone is what the updater manages."""
    from core import updater

    origin, publisher, app_dir = tmp_path / "origin.git", tmp_path / "publisher", tmp_path / "app"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(publisher))
    _commit_file(publisher, "requirements.txt", "requests\n", "initial")
    _git(publisher, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(app_dir))
    monkeypatch.setattr(updater, "REPO_DIR", str(app_dir))
    return {"publisher": str(publisher), "app": str(app_dir)}


def _publish(git_repos, name, content, message):
    _commit_file(git_repos["publisher"], name, content, message)
    _git(git_repos["publisher"], "push", "origin", "HEAD:main")


def test_updater_fast_forwards_to_new_commits(git_repos):
    from core import updater

    assert updater.status()["behind"] == 0
    _publish(git_repos, "feature.txt", "new", "Add feature")
    state = updater.status()
    assert state["behind"] == 1
    assert state["commits"][0]["subject"] == "Add feature"
    result = updater.apply(lambda line: None)
    assert result["updated"] is True
    assert os.path.exists(os.path.join(git_repos["app"], "feature.txt"))
    assert updater.status()["behind"] == 0
    assert updater.apply(lambda line: None) == {"updated": False}


def test_updater_refuses_when_there_are_local_changes(git_repos):
    from core import updater

    _publish(git_repos, "feature.txt", "new", "Add feature")
    with open(os.path.join(git_repos["app"], "requirements.txt"), "a", encoding="utf-8") as handle:
        handle.write("my-local-edit\n")
    assert updater.status()["local_changes"] == ["requirements.txt"]  # full name, nothing clipped
    with pytest.raises(RuntimeError, match="Local changes"):
        updater.apply(lambda line: None)
    assert not os.path.exists(os.path.join(git_repos["app"], "feature.txt"))


def test_updater_reinstalls_packages_and_rolls_back_on_failure(git_repos, monkeypatch):
    from core import updater

    installs = []
    monkeypatch.setattr(updater, "install_requirements", lambda log: installs.append("ok"))
    _publish(git_repos, "requirements.txt", "requests\nnewdep\n", "Add dependency")
    assert updater.apply(lambda line: None)["updated"] is True
    assert installs == ["ok"]

    def failing_install(log):
        raise RuntimeError("pip install failed")

    monkeypatch.setattr(updater, "install_requirements", failing_install)
    before = updater.status(fetch=False)["current"]["hash"]
    _publish(git_repos, "requirements.txt", "requests\nnewdep\nbroken\n", "Break install")
    with pytest.raises(RuntimeError, match="pip install failed"):
        updater.apply(lambda line: None)
    assert updater.status(fetch=False)["current"]["hash"] == before  # rolled back


def test_updater_reports_when_not_a_git_clone(tmp_path, monkeypatch):
    from core import updater

    monkeypatch.setattr(updater, "REPO_DIR", str(tmp_path))
    state = updater.status()
    assert state["available"] is False
    assert "not a git clone" in state["reason"]


def test_update_endpoint_restarts_and_resumes(fake_server, fake_playit, fake_cloudflared, monkeypatch):
    from core import updater

    restarts = []
    monkeypatch.setattr(updater, "apply", lambda log: {"updated": True, "from": "aaaaaaa", "to": "bbbbbbb", "changed_files": []})
    monkeypatch.setattr(updater, "restart_process", lambda: restarts.append("restart"))
    client.post("/api/server/start", auth=("admin", "s3cret-pass"))

    response = client.post("/api/update/apply", auth=("admin", "s3cret-pass"))
    assert response.json()["state"] == "updating"
    assert _wait_until(lambda: restarts == ["restart"], timeout=5)
    assert not fake_server[0].is_running()  # stopped cleanly before the restart
    assert app.state.runtime.update_state == "restarting"

    # The new process reads the marker and brings the server back.
    app.state.runtime.shutting_down = False
    app.state.runtime.update_state = "idle"
    monkeypatch.delenv("EZ_NO_AUTOSTART", raising=False)
    app.state.autostart()
    assert _wait_until(lambda: len(fake_server) == 2 and fake_server[1].is_running())
    assert not os.path.exists(os.path.join(paths.DATA_DIR, "resume_after_update.json"))


def test_update_failure_is_reported(monkeypatch):
    from core import updater

    def failing_apply(log):
        raise RuntimeError("Local changes would be overwritten (core/app.py).")

    monkeypatch.setattr(updater, "apply", failing_apply)
    app.state.runtime.update_state = "idle"
    client.post("/api/update/apply", auth=("admin", "s3cret-pass"))
    assert _wait_until(lambda: app.state.runtime.update_state == "error")
    payload = client.get("/api/update", auth=("admin", "s3cret-pass")).json()
    assert "Local changes" in payload["error"]
    app.state.runtime.update_state = "idle"
    app.state.runtime.update_error = ""


def test_dashboard_assets_are_cache_busted():
    text = client.get("/dashboard/setup").text
    assert f"/dashboard.js?v={webapp_module.ASSET_VERSION}" in text
    assert 'id="update-apply-button"' in text


# ---------- Worlds ----------
def _zip_bytes(entries):
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _write_zip(path, entries):
    with open(path, "wb") as handle:
        handle.write(_zip_bytes(entries))
    return str(path)


def test_world_import_finds_nested_singleplayer_save(tmp_path):
    from core import worlds

    sdir = tmp_path / "server"
    sdir.mkdir()
    zip_path = _write_zip(tmp_path / "w.zip", {
        "saves/My World/level.dat": "x",
        "saves/My World/region/r.0.0.mca": "chunk",
        "saves/My World/DIM-1/region/r.0.0.mca": "nether",
        "__MACOSX/saves/My World/._level.dat": "junk",
    })
    folder = worlds.import_zip(str(sdir), zip_path, "My World")
    assert folder == "my-world"
    assert (sdir / "my-world" / "region" / "r.0.0.mca").read_text() == "chunk"
    assert (sdir / "my-world" / "DIM-1" / "region" / "r.0.0.mca").exists()
    listed = worlds.list_worlds(str(sdir), "world")
    entry = next(w for w in listed if w["folder"] == "my-world")
    assert entry["name"] == "My World" and entry["exists"] and entry["source"] == "upload"


def test_world_import_keeps_paper_dimensions_together(tmp_path):
    from core import worlds

    sdir = tmp_path / "server"
    sdir.mkdir()
    zip_path = _write_zip(tmp_path / "p.zip", {
        "world/level.dat": "x", "world_nether/level.dat": "n", "world_the_end/level.dat": "e",
    })
    folder = worlds.import_zip(str(sdir), zip_path, "Paper world")
    assert (sdir / f"{folder}_nether" / "level.dat").exists()
    assert (sdir / f"{folder}_the_end" / "level.dat").exists()
    folders = [w["folder"] for w in worlds.list_worlds(str(sdir), folder)]
    assert folders == [folder]  # dimension folders are not listed as separate worlds


def test_world_import_rejects_bad_zips(tmp_path):
    from core import worlds

    sdir = tmp_path / "server"
    sdir.mkdir()
    with pytest.raises(ValueError, match="No Minecraft world"):
        worlds.import_zip(str(sdir), _write_zip(tmp_path / "a.zip", {"readme.txt": "hi"}), "A")
    with pytest.raises(ValueError, match="Unsafe"):
        worlds.import_zip(str(sdir), _write_zip(tmp_path / "b.zip", {"w/level.dat": "x", "w/../../evil.txt": "x"}), "B")
    assert not (tmp_path / "evil.txt").exists()
    assert os.listdir(sdir) == []  # failed imports leave nothing behind


def test_worlds_endpoints_create_switch_rename_delete(monkeypatch):
    original = webapp_module._effective_config
    app.state.runtime.process = None
    sdir = webapp_module._server_dir_for_config(original())
    os.makedirs(os.path.join(sdir, "world"), exist_ok=True)  # the default world, as after a first start
    with open(os.path.join(sdir, "world", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("x")
    created = client.post("/api/worlds", auth=("admin", "s3cret-pass"), json={"name": "Survival 2", "seed": "123"}).json()
    folder = created["folder"]
    entry = next(w for w in created["worlds"] if w["folder"] == folder)
    assert entry["exists"] is False and entry["seed"] == "123"

    client.post(f"/api/worlds/{folder}/activate", auth=("admin", "s3cret-pass"))
    config = original()
    assert config["level_name"] == folder and config["level_seed"] == "123"
    assert webapp_module._server_properties_from_config(config)["level-name"] == folder

    renamed = client.post(f"/api/worlds/{folder}/rename", auth=("admin", "s3cret-pass"), json={"name": "Hardcore"}).json()
    assert next(w for w in renamed["worlds"] if w["folder"] == folder)["name"] == "Hardcore"

    response = client.delete(f"/api/worlds/{folder}", auth=("admin", "s3cret-pass"))
    assert response.status_code == 409  # active world can't be deleted
    client.post("/api/worlds/world/activate", auth=("admin", "s3cret-pass"))
    response = client.delete(f"/api/worlds/{folder}", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert folder not in [w["folder"] for w in response.json()["worlds"]]
    assert client.post("/api/worlds/../x/activate", auth=("admin", "s3cret-pass")).status_code in {400, 404}


def test_world_upload_download_and_switch_restarts_running_server(fake_server):
    sdir = webapp_module._server_dir_for_config(webapp_module._effective_config())
    os.makedirs(os.path.join(sdir, "world"), exist_ok=True)
    with open(os.path.join(sdir, "world", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("x")
    upload = client.post("/api/worlds/upload", auth=("admin", "s3cret-pass"),
                         files={"file": ("Island.zip", _zip_bytes({"Island/level.dat": "x", "Island/region/a.mca": "a"}),
                                         "application/zip")},
                         data={"name": "Island"})
    assert upload.status_code == 200
    folder = upload.json()["folder"]

    download = client.get(f"/api/worlds/{folder}/download", auth=("admin", "s3cret-pass"))
    assert download.status_code == 200
    import io
    assert f"{folder}/region/a.mca" in zipfile.ZipFile(io.BytesIO(download.content)).namelist()

    client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    result = client.post(f"/api/worlds/{folder}/activate", auth=("admin", "s3cret-pass")).json()
    assert "restarted" in result["message"]
    assert len(fake_server) == 2 and not fake_server[0].is_running() and fake_server[1].is_running()
    client.post("/api/worlds/world/activate", auth=("admin", "s3cret-pass"))


def test_backup_follows_the_active_world(monkeypatch):
    original = webapp_module._effective_config
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {**original(), "level_name": "skyblock"})
    app.state.runtime.process = None
    sdir = webapp_module._server_dir_for_config(original())
    os.makedirs(os.path.join(sdir, "skyblock"), exist_ok=True)
    with open(os.path.join(sdir, "skyblock", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("v1")
    backup = client.post("/api/server/backup", auth=("admin", "s3cret-pass")).json()
    assert backup["ok"] and "world-backup-skyblock-" in backup["archive"]
    with open(os.path.join(sdir, "skyblock", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("v2")
    restored = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"),
                           json={"backup_path": backup["archive"]})
    assert restored.status_code == 200
    with open(os.path.join(sdir, "skyblock", "level.dat"), encoding="utf-8") as handle:
        assert handle.read() == "v1"
    assert any(name.startswith("skyblock-pre-restore-") for name in os.listdir(sdir))


# ---------- Mod browser ----------
@pytest.fixture
def fake_modrinth(monkeypatch):
    """A tiny fake of the Modrinth API: 'sodium' requires 'fabric-api'; downloads write small files."""
    from core import modrinth

    projects = {
        "sodium": {"id": "P1", "slug": "sodium", "title": "Sodium", "project_type": "mod", "body": "# Fast\n**Very** fast [docs](https://x)"},
        "P1": None,
        "fabric-api": {"id": "P2", "slug": "fabric-api", "title": "Fabric API", "project_type": "mod", "body": ""},
        "P2": None,
    }
    projects["P1"], projects["P2"] = projects["sodium"], projects["fabric-api"]
    versions = {
        "P1": [{"version_number": "0.6", "files": [{"url": "u1", "filename": "sodium.jar", "primary": True}],
                "dependencies": [{"dependency_type": "required", "project_id": "P2"},
                                 {"dependency_type": "optional", "project_id": "P9"}]}],
        "P2": [{"version_number": "0.100", "files": [{"url": "u2", "filename": "fabric-api.jar", "primary": True}],
                "dependencies": []}],
    }
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/search":
            return {"hits": [{"project_id": "P1", "slug": "sodium", "title": "Sodium", "server_side": "optional"}],
                    "total_hits": 1, "offset": 0, "limit": 20}
        if path == "/tag/category":
            return [{"name": "optimization", "project_type": "mod", "header": "categories"},
                    {"name": "16x", "project_type": "resourcepack", "header": "resolutions"}]
        if path.endswith("/version"):
            return versions[path.split("/")[2]]
        return projects[path.split("/")[2]]

    def fake_download(url, dest, log=print, progress=None):
        with open(dest, "w", encoding="utf-8") as handle:
            handle.write(url)

    monkeypatch.setattr(modrinth, "_get", fake_get)
    monkeypatch.setattr(modrinth, "download", fake_download)
    modrinth._categories_cache.update(at=0.0, items=[])
    original = webapp_module._effective_config
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {**original(), "loader": "fabric", "version": "1.21.7"})
    app.state.runtime.process = None
    return calls


def test_mod_browser_lists_popular_server_mods_without_a_query(fake_modrinth):
    response = client.get("/api/modrinth/search", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert response.json()["hits"][0]["slug"] == "sodium"
    params = fake_modrinth[-1][1]
    assert params["index"] == "downloads"
    facets = json.loads(params["facets"])
    assert ["server_side:required", "server_side:optional"] in facets
    assert ["categories:fabric"] in facets and ["versions:1.21.7"] in facets
    assert client.get("/api/modrinth/categories", auth=("admin", "s3cret-pass")).json()["categories"] == ["optimization"]


def test_mod_install_brings_required_dependencies_and_can_be_removed(fake_modrinth):
    response = client.post("/api/modrinth/install", auth=("admin", "s3cret-pass"), json={"slug": "sodium"})
    payload = response.json()
    assert payload["filename"] == "sodium.jar"
    assert payload["dependencies"] == ["Fabric API"]
    mods = client.get("/api/server/mods", auth=("admin", "s3cret-pass")).json()
    assert {m["name"] for m in mods["mods"]} == {"sodium.jar", "fabric-api.jar"}
    assert set(mods["project_ids"]) == {"P1", "P2"}

    details = client.get("/api/modrinth/project/sodium", auth=("admin", "s3cret-pass")).json()
    assert details["compatible_versions"] == 1 and details["body"].startswith("Fast")
    assert "<" not in details["body"] and "**" not in details["body"]

    removed = client.delete("/api/server/mods/sodium.jar", auth=("admin", "s3cret-pass"))
    assert removed.status_code == 200
    mods = client.get("/api/server/mods", auth=("admin", "s3cret-pass")).json()
    assert [m["name"] for m in mods["mods"]] == ["fabric-api.jar"]


def test_mod_browser_explains_vanilla(monkeypatch):
    original = webapp_module._effective_config
    monkeypatch.setattr(webapp_module, "_effective_config", lambda: {**original(), "loader": "vanilla"})
    response = client.get("/api/modrinth/search", auth=("admin", "s3cret-pass"))
    assert response.status_code == 400
    assert "Vanilla" in response.json()["detail"]
