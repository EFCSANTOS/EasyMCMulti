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

from core import cloudflared, config_store, downloaders, fsutil, java_manager, modrinth, notifier, paths, playit
from webapp import auth
from webapp.app import create_app

webapp_module = importlib.import_module("webapp.app")
# Shared helpers and constants the routers use as common.NAME: patch them here.
common_module = importlib.import_module("webapp.common")
layout_module = importlib.import_module("webapp.pages.layout")

app = create_app()
client = TestClient(app)


def _dashboard_js():
    """All dashboard scripts, in load order."""
    js_dir = os.path.join(os.path.dirname(webapp_module.__file__), "static", "js")
    return "\n".join(open(os.path.join(js_dir, name), encoding="utf-8").read() for name in layout_module.SCRIPTS)


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
    app.state.sessions.clear()
    app.state.login_limiter.clear()
    client.cookies.clear()


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
    source = _dashboard_js()
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

    monkeypatch.setattr(modrinth, "install", fake_install)
    monkeypatch.setattr(common_module, "_effective_config", lambda: {"loader": "fabric", "version": "1.21.7"})

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
    monkeypatch.setattr(playit, "ensure_agent", lambda log=print: "agent")
    monkeypatch.setattr(playit, "PlayitAgent", DummyAgent)
    monkeypatch.setattr(playit, "ensure_tunnel", lambda secret, port, log, timeout_sec=120, should_abort=None: "demo.example:25565")
    monkeypatch.setattr(playit, "kill_stale_agents", lambda log=print: 0)
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

    monkeypatch.setattr(playit, "claim_interactive", fake_claim)
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
    monkeypatch.setattr(playit, "claim_interactive",
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
    monkeypatch.setattr(cloudflared, "ensure_binary", lambda log=print: "cloudflared")
    monkeypatch.setattr(cloudflared, "QuickTunnel", FakeQuickTunnel)
    monkeypatch.setattr(common_module, "WEB_TUNNEL_RETRY_SEC", 0)
    yield FakeQuickTunnel.instances
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": False})


def test_remote_dashboard_toggle(fake_cloudflared):
    response = client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert response.json()["enabled"] is True
    assert _wait_until(lambda: client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()["state"] == "running")
    status = client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()
    assert status["url"] == "https://demo-1.trycloudflare.com"
    assert fake_cloudflared[0].port == common_module.WEB_PORT

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

    monkeypatch.setattr(cloudflared, "ensure_binary", missing)
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

    monkeypatch.setattr(common_module, "psutil", BlockedPsutil)
    snapshot = common_module._system_metrics_snapshot()
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
    monkeypatch.setattr(playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(playit.platform, "machine", lambda: "aarch64")
    assert playit._agent_binary_name() == "playit-linux-aarch64"

    monkeypatch.setattr(playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(playit.platform, "machine", lambda: "x86_64")
    assert playit._agent_binary_name() == "playit-linux-amd64"

    monkeypatch.setattr(playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(playit.platform, "machine", lambda: "armv7l")
    assert playit._agent_binary_name() == "playit-linux-armv7"


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


def test_server_lifecycle_start_and_stop():
    start_response = _start()
    assert start_response.status_code in {200, 202}
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
    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: True)
    monkeypatch.setattr(java_manager, "ensure_java", lambda required, log=print: "java")
    monkeypatch.setattr(downloaders, "launch_args", lambda *args, **kwargs: ["java"])
    monkeypatch.setattr(common_module, "update_properties", lambda server_dir, **kwargs: None)
    monkeypatch.setattr(common_module, "ServerProcess", RecordingProcess)
    monkeypatch.setattr(common_module, "CRASH_RESTART_DELAY_SEC", 0)
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


def _lifecycle_free():
    lock = app.state.runtime.lifecycle
    if not lock.acquire(blocking=False):
        return False
    lock.release()
    return True


def _wait_idle(timeout=3.0):
    """Wait for the background lifecycle job (create/start/restart/world switch) to finish."""
    assert _wait_until(_lifecycle_free, timeout), "lifecycle job did not finish"


def _start(**kwargs):
    response = client.post("/api/server/start", auth=("admin", "s3cret-pass"), **kwargs)
    _wait_idle()
    return response


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


def test_always_on_settings_render_on_setup_page():
    text = client.get("/dashboard/setup").text
    for element_id in ("autostart-server", "auto-restart", "autostart-playit", "always-on-save-button"):
        assert f'id="{element_id}"' in text


def test_mod_upload_rejected_for_vanilla(monkeypatch):
    monkeypatch.setattr(common_module, "_effective_config", lambda: {"loader": "vanilla", "version": "1.21.7"})
    response = client.post(
        "/api/server/mods/upload",
        auth=("admin", "s3cret-pass"),
        files={"files": ("demo.jar", b"jar-bytes", "application/java-archive")},
    )
    assert response.status_code == 400
    assert "vanilla" in response.json()["detail"]


def test_player_tracking_from_console_lines():
    runtime = common_module.RuntimeState()
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
    monkeypatch.setattr(common_module, "_effective_config", lambda: {"loader": "vanilla", "version": "1.21.7"})
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
    monkeypatch.setattr(playit, "load_config", lambda: {"secret": "top-secret", "address": "demo.example"})
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

    monkeypatch.setattr(playit, "claim_interactive", fake_claim)
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
    monkeypatch.setattr(playit, "load_config", lambda: {"secret": "demo-secret", "address": "demo.example"})
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
    _start()

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
    for name in layout_module.SCRIPTS:
        assert f"/js/{name}?v={layout_module.ASSET_VERSION}" in text
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
    original = common_module._effective_config
    app.state.runtime.process = None
    sdir = common_module._server_dir_for_config(original())
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
    assert common_module._server_properties_from_config(config)["level-name"] == folder

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
    sdir = common_module._server_dir_for_config(common_module._effective_config())
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

    _start()
    switch = client.post(f"/api/worlds/{folder}/activate", auth=("admin", "s3cret-pass"))
    assert switch.status_code == 202 and "Switching" in switch.json()["message"]
    _wait_idle()
    assert "restarted" in app.state.runtime.operation_message
    assert len(fake_server) == 2 and not fake_server[0].is_running() and fake_server[1].is_running()
    client.post("/api/worlds/world/activate", auth=("admin", "s3cret-pass"))
    _wait_idle()


def test_backup_follows_the_active_world(monkeypatch):
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "level_name": "skyblock"})
    app.state.runtime.process = None
    sdir = common_module._server_dir_for_config(original())
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
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "loader": "fabric", "version": "1.21.7"})
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
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "loader": "vanilla"})
    response = client.get("/api/modrinth/search", auth=("admin", "s3cret-pass"))
    assert response.status_code == 400
    assert "Vanilla" in response.json()["detail"]


# ---------- Login sessions and lockout (FIX-01) ----------
def _login(browser, password="s3cret-pass"):
    return browser.post("/api/login", json={"username": "admin", "password": password})


def test_login_sets_session_cookie_that_authenticates():
    browser = TestClient(app)
    response = _login(browser)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "ezmc_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert browser.get("/api/status").status_code == 200
    assert browser.get("/api/session").json() == {"username": "admin"}


def test_login_page_does_not_store_credentials_in_browser_storage():
    text = client.get("/login").text
    assert "sessionStorage" not in text and "localStorage" not in text
    assert "/api/login" in text


def test_wrong_passwords_lock_out_the_ip_even_for_the_right_password():
    browser = TestClient(app)
    first = _login(browser, "wrong")
    assert first.status_code == 401
    assert "www-authenticate" not in first.headers  # no native browser password dialog
    assert client.get("/api/status", auth=("admin", "bad")).headers["www-authenticate"] == "Basic"
    for _ in range(auth.LOCKOUT_FAILURES - 2):
        assert _login(browser, "wrong").status_code == 401
    response = _login(browser, "wrong")
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert _login(browser).status_code == 429
    # Basic auth shares the same counter.
    assert browser.get("/api/status", auth=("admin", "s3cret-pass")).status_code == 429


def test_lockout_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(app.state.login_limiter, "clock", lambda: now[0])
    browser = TestClient(app)
    for _ in range(auth.LOCKOUT_FAILURES):
        _login(browser, "wrong")
    assert _login(browser).status_code == 429
    now[0] += auth.LOCKOUT_SEC + 1
    assert _login(browser).status_code == 200


def test_lockout_is_per_ip_and_spoofed_forwarding_header_is_ignored():
    attacker = TestClient(app, client=("203.0.113.5", 5000))
    for index in range(auth.LOCKOUT_FAILURES + 1):
        attacker.post("/api/login", json={"username": "admin", "password": "wrong"},
                      headers={"CF-Connecting-IP": f"198.51.100.{index}"})
    assert _login(attacker).status_code == 429
    assert _login(TestClient(app, client=("192.168.1.20", 5000))).status_code == 200


def test_cloudflared_forwarded_ip_is_used_for_lockout():
    tunnel = TestClient(app, client=("127.0.0.1", 5000))
    for _ in range(auth.LOCKOUT_FAILURES):
        tunnel.post("/api/login", json={"username": "admin", "password": "wrong"},
                    headers={"CF-Connecting-IP": "198.51.100.7"})
    locked = tunnel.post("/api/login", json={"username": "admin", "password": "s3cret-pass"},
                         headers={"CF-Connecting-IP": "198.51.100.7"})
    assert locked.status_code == 429
    other = tunnel.post("/api/login", json={"username": "admin", "password": "s3cret-pass"},
                        headers={"CF-Connecting-IP": "198.51.100.8"})
    assert other.status_code == 200
    assert "Secure" in other.headers["set-cookie"]


def test_global_failure_ceiling_locks_all_ips():
    limiter = auth.LoginLimiter(clock=lambda: 50.0)
    for index in range(auth.GLOBAL_FAILURE_LIMIT):
        limiter.record_failure(f"10.0.0.{index}")
    assert limiter.retry_after("10.9.9.9") > 0


def test_first_lockout_sends_security_alert(sent_emails):
    browser = TestClient(app)
    for _ in range(auth.LOCKOUT_FAILURES + 2):
        _login(browser, "wrong")
    assert _wait_until(lambda: any("Sign-in locked" in subject for subject, _ in sent_emails))
    assert sum("Sign-in locked" in subject for subject, _ in sent_emails) == 1


def test_logout_invalidates_the_session():
    browser = TestClient(app)
    _login(browser)
    token = browser.cookies.get("ezmc_session")
    assert browser.post("/api/logout").status_code == 200
    assert browser.get("/api/status").status_code == 401
    replay = TestClient(app, cookies={"ezmc_session": token})
    assert replay.get("/api/status").status_code == 401


def test_session_expires_after_inactivity(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(app.state.sessions, "clock", lambda: now[0])
    browser = TestClient(app)
    _login(browser)
    now[0] += auth.SESSION_TTL_SEC - 10
    assert browser.get("/api/status").status_code == 200  # slides the expiry
    now[0] += auth.SESSION_TTL_SEC - 10
    assert browser.get("/api/status").status_code == 200
    now[0] += auth.SESSION_TTL_SEC + 1
    assert browser.get("/api/status").status_code == 401


def test_cookie_session_rejects_cross_site_and_non_json_writes():
    browser = TestClient(app)
    _login(browser)
    assert browser.post("/api/server/backup", content=b"x=1",
                        headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 403
    assert browser.post("/api/remote-dashboard", json={"enabled": False},
                        headers={"Origin": "http://evil.example"}).status_code == 403
    response = browser.post("/api/remote-dashboard", json={"enabled": False}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200


def test_websocket_console_accepts_session_cookie():
    browser = TestClient(app)
    _login(browser)
    app.state.console_manager.history.clear()
    with browser.websocket_connect("/ws/console") as websocket:
        app.state.console_manager.publish("hello over cookie")
        assert websocket.receive_text() == "hello over cookie"
    try:
        with browser.websocket_connect("/ws/console", headers={"Origin": "http://testserver:9999"}):
            pass
    except Exception:
        return
    raise AssertionError("A cookie WebSocket from another origin should be rejected.")


def test_remote_dashboard_reports_weak_password(monkeypatch):
    monkeypatch.setenv("EZ_WEBAPP_PASSWORD", "short")
    assert client.get("/api/remote-dashboard", auth=("admin", "short")).json()["weak_password"] is True
    monkeypatch.setenv("EZ_WEBAPP_PASSWORD", "a-much-longer-password")
    status = client.get("/api/remote-dashboard", auth=("admin", "a-much-longer-password")).json()
    assert status["weak_password"] is False
    assert 'id="remote-dashboard-weak-password"' in client.get("/dashboard/remote").text


# ---------- Background lifecycle jobs and the lifecycle lock (FIX-02 / FIX-04) ----------
@pytest.fixture
def slow_install(fake_server, monkeypatch):
    """Installs block until the test sets the returned event."""
    gate = threading.Event()

    def prepare(loader, version, target_dir, java_exe, log=print, progress=None):
        if progress:
            progress(10, 100)
        if not gate.wait(5):
            raise RuntimeError("test gate never opened")

    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: False)
    monkeypatch.setattr(downloaders, "prepare", prepare)
    monkeypatch.setattr(common_module, "accept_eula", lambda target_dir: None)
    yield gate
    gate.set()
    _wait_idle()


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


# ---------- Atomic writes and damaged-file recovery (FIX-03) ----------
def test_config_save_is_atomic_and_keeps_a_backup():
    config_store.save({"loader": "paper", "version": "1.21.4"})
    config_store.save({"loader": "fabric", "version": "1.21.4"})
    path = config_store._FILE
    assert config_store.load()["loader"] == "fabric"
    assert json.load(open(path + ".bak", encoding="utf-8"))["loader"] == "paper"
    assert not os.path.exists(path + ".tmp")


def test_damaged_config_is_restored_from_backup_and_logged():
    config_store.save({"loader": "paper", "version": "1.21.4"})
    config_store.save({"loader": "paper", "version": "1.21.4", "motd": "x"})
    with open(config_store._FILE, "w", encoding="utf-8") as handle:
        handle.write('{"loader": "pap')  # power cut mid-write (pre-FIX-03 style)
    app.state.console_manager.history.clear()
    assert config_store.load()["loader"] == "paper"
    assert json.load(open(config_store._FILE, encoding="utf-8"))["loader"] == "paper"  # repaired on disk
    lines = client.get("/api/console", auth=("admin", "s3cret-pass")).json()["lines"]
    assert any("launcher_config.json was damaged; restored the previous copy" in line for line in lines)


def test_failed_write_leaves_the_original_intact(monkeypatch):
    config_store.save({"loader": "paper"})

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch, pytest.raises(OSError):
        patch.setattr(fsutil.os, "replace", broken_replace)
        config_store.save({"loader": "fabric"})
    assert config_store.load()["loader"] == "paper"
    assert not os.path.exists(config_store._FILE + ".tmp")


def test_put_config_reports_disk_errors(monkeypatch):
    def full_disk(data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config_store, "save", full_disk)
    response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={"motd": "hi"})
    assert response.status_code == 500
    assert "disk full" in response.json()["detail"]


def test_missing_file_without_backup_is_quiet(tmp_path):
    app.state.console_manager.history.clear()
    assert fsutil.read_json(str(tmp_path / "nothing.json"), {"a": 1}) == {"a": 1}
    assert not any("damaged" in line for line in app.state.console_manager.history)


def test_atomic_writes_cover_every_settings_file(tmp_path):
    notifier.save_config({"email_to": "me@example.com"})
    notifier.save_config({"email_to": "you@example.com"})
    playit.save_config("ab12cd34ef", "a.example")
    playit.save_config("ab12cd34ef", "b.example")
    from core import modrinth, server_process, worlds
    worlds._save_meta(str(tmp_path), {"world": {"name": "One"}})
    worlds._save_meta(str(tmp_path), {"world": {"name": "Two"}})
    modrinth._save_record(str(tmp_path), {"p1": {"filename": "a.jar"}})
    modrinth._save_record(str(tmp_path), {"p1": {"filename": "b.jar"}})
    server_process.update_properties(str(tmp_path), motd="one")
    server_process.update_properties(str(tmp_path), motd="two")
    for path in (notifier._CONFIG_FILE, playit._CONFIG_FILE, str(tmp_path / worlds.META_FILE),
                 str(tmp_path / modrinth.RECORD_FILE), str(tmp_path / "server.properties")):
        assert os.path.exists(path + ".bak"), path
        assert not os.path.exists(path + ".tmp"), path
    assert server_process.read_properties(str(tmp_path))["motd"] == "two"
    assert worlds.load_meta(str(tmp_path))["world"]["name"] == "Two"


# ---------- Input validation (FIX-05) ----------
def test_player_names_with_newlines_are_rejected(fake_server):
    _start()
    for bad in ("x\nstop", "a b", "ab", "x" * 17, "Steve;op", ""):
        response = client.post("/api/admin/kick", auth=("admin", "s3cret-pass"), json={"player": bad})
        assert response.status_code == 400, bad
    assert fake_server[0].sent == []
    for good in ("Steve", "Alex_99", ".BedrockGuy"):
        assert client.post("/api/admin/op", auth=("admin", "s3cret-pass"), json={"player": good}).status_code == 200
    assert fake_server[0].sent == ["op Steve", "op Alex_99", "op .BedrockGuy"]


def test_console_commands_must_be_one_line(fake_server):
    _start()
    for bad in ("say hi\nstop", "say hi\rstop", "say \x00", "say " + "x" * 300):
        response = client.post("/api/server/command", auth=("admin", "s3cret-pass"), json={"command": bad})
        assert response.status_code == 400, repr(bad)
    assert fake_server[0].sent == []
    assert client.post("/api/server/command", auth=("admin", "s3cret-pass"),
                       json={"command": "say hello"}).status_code == 200


def test_config_rejects_bad_values_naming_the_field():
    cases = {"ram_mb": "lots", "server_port": 80, "max_players": -1, "view_distance": 99,
             "gamemode": "god", "motd": "line one\nline two", "version": "../../etc", "level_name": "../x",
             "loader": "bukkit"}
    for key, value in cases.items():
        response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={key: value})
        assert response.status_code == 422, key
        detail = response.json()["detail"]
        assert detail[0]["loc"] == ["body", key] and key in detail[0]["msg"]
    response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={"surprise": 1})
    assert response.status_code == 422 and response.json()["detail"][0]["loc"] == ["body", "surprise"]
    assert config_store.load() == {}  # nothing was saved


def test_config_ram_is_capped_by_host_memory(monkeypatch):
    monkeypatch.setattr(common_module, "_host_ram_mb", lambda: 4096)
    response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={"ram_mb": 8192})
    assert response.status_code == 422 and "4096" in response.json()["detail"][0]["msg"]
    assert client.put("/api/config", auth=("admin", "s3cret-pass"), json={"ram_mb": 3072}).status_code == 200


def test_start_payload_is_validated_too(fake_server):
    response = client.post("/api/server/start", auth=("admin", "s3cret-pass"), json={"server_port": "nope"})
    assert response.status_code == 422
    assert fake_server == []


def test_stored_garbage_falls_back_to_defaults(fake_server):
    config_store.save({"ram_mb": "lots", "server_port": 99999, "motd": "kept", "custom": "x"})
    app.state.console_manager.history.clear()
    config = client.get("/api/config", auth=("admin", "s3cret-pass")).json()
    assert config["ram_mb"] == 2048 and config["server_port"] == 25565
    assert config["motd"] == "kept" and config["custom"] == "x"
    assert any("Ignoring invalid saved value ram_mb='lots'" in line for line in app.state.console_manager.history)
    assert _start().status_code == 202
    assert app.state.runtime.state == "running"


def test_dashboard_highlights_rejected_fields():
    source = _dashboard_js()
    assert "markInvalidFields" in source and "aria-invalid" in source


# ---------- Players page: whitelist, operators, bans (FEAT-02) ----------
players_route = importlib.import_module("webapp.routes.players")
AUTH = ("admin", "s3cret-pass")


def _server_file(name):
    return os.path.join(common_module._server_dir_for_config(common_module._effective_config()), name)


def test_stopped_server_whitelist_edits_json_with_offline_uuid():
    config_store.save({"online_mode": False})
    response = client.post("/api/players/whitelist", auth=AUTH, json={"action": "add", "name": "Notch"})
    assert response.status_code == 200 and response.json()["whitelist"] == [{"name": "Notch"}]
    entries = json.load(open(_server_file("whitelist.json"), encoding="utf-8"))
    assert entries == [{"uuid": "b50ad385-829d-3141-a216-7e7d7539ba7f", "name": "Notch"}]
    assert client.post("/api/players/whitelist", auth=AUTH, json={"action": "remove", "name": "notch"}).json()["whitelist"] == []
    assert client.post("/api/players/whitelist", auth=AUTH, json={"action": "remove", "name": "Notch"}).status_code == 404


def test_stopped_server_online_mode_resolves_uuid_from_mojang(monkeypatch):
    from core import players

    class FakeResponse:
        def __init__(self, status, data=None):
            self.status_code, self.ok, self._data = status, status == 200, data

        def json(self):
            return self._data

    def fake_get(url, timeout):
        if url.endswith("/steve"):
            return FakeResponse(200, {"id": "8667ba71b85a4004af54457a9734eed7", "name": "Steve"})
        return FakeResponse(404)

    monkeypatch.setattr(players.requests, "get", fake_get)
    result = client.post("/api/players/ops", auth=AUTH, json={"action": "add", "name": "steve", "level": 2}).json()
    assert result["ops"] == [{"name": "Steve", "level": 2}]
    entry = json.load(open(_server_file("ops.json"), encoding="utf-8"))[0]
    assert entry["uuid"] == "8667ba71-b85a-4004-af54-457a9734eed7" and entry["level"] == 2
    response = client.post("/api/players/whitelist", auth=AUTH, json={"action": "add", "name": "NoSuchGuy"})
    assert response.status_code == 400 and "no Minecraft account" in response.json()["detail"]


def test_running_server_changes_lists_with_commands(fake_server, monkeypatch):
    monkeypatch.setattr(players_route, "SYNC_WAIT_SEC", 0.05)
    _start()
    for kind, body in (("whitelist", {"action": "add", "name": "Steve"}), ("ops", {"action": "add", "name": "Alex"}),
                       ("bans", {"action": "add", "name": "Griefer", "reason": "broke the base"}),
                       ("bans", {"action": "remove", "name": "Griefer"}), ("ips", {"action": "remove", "ip": "10.0.0.7"}),
                       ("whitelist", {"action": "enable"})):
        assert client.post(f"/api/players/{kind}", auth=AUTH, json=body).status_code == 200, (kind, body)
    assert fake_server[0].sent == ["whitelist add Steve", "op Alex", "ban Griefer broke the base", "pardon Griefer",
                                   "pardon-ip 10.0.0.7", "whitelist on"]
    assert common_module._effective_config()["white_list"] is True


def test_player_list_input_is_validated():
    cases = [("whitelist", {"action": "add", "name": "x\nop me"}), ("whitelist", {"action": "add", "name": "ab"}),
             ("ops", {"action": "add", "name": "Steve", "level": 7}), ("ips", {"action": "remove", "ip": "999.1.1.1"}),
             ("bans", {"action": "add", "name": "Steve", "reason": "line\nbreak"}), ("whitelist", {"action": "nuke"})]
    for kind, body in cases:
        assert client.post(f"/api/players/{kind}", auth=AUTH, json=body).status_code == 400, body
    assert client.post("/api/players/admins", auth=AUTH, json={"action": "add", "name": "Steve"}).status_code == 404


def test_player_lists_wait_for_running_lifecycle_job():
    config_store.save({"online_mode": False})
    lock = app.state.runtime.lifecycle
    lock.acquire()
    try:
        response = client.post("/api/players/whitelist", auth=AUTH, json={"action": "add", "name": "Steve"})
        assert response.status_code == 409
    finally:
        lock.release()


def test_refused_players_show_up_as_recently_seen():
    runtime = app.state.runtime
    runtime.recent_players.clear()
    runtime.note_player("Stranger", rejected=True)
    runtime.track_player_event("[12:00:00] [Server thread/INFO]: Steve joined the game")
    recent = client.get("/api/players/lists", auth=AUTH).json()["recent"]
    assert [(p["name"], p["rejected"], p["whitelisted"]) for p in recent] == [("Steve", False, False),
                                                                              ("Stranger", True, False)]


def test_server_output_records_whitelist_refusals(fake_server, monkeypatch):
    captured = {}
    original = RecordingProcess.__init__

    def capture(self, args, cwd, on_output, on_exit):
        captured["on_output"] = on_output
        original(self, args, cwd, on_output, on_exit)

    monkeypatch.setattr(RecordingProcess, "__init__", capture)
    app.state.runtime.recent_players.clear()
    _start()
    captured["on_output"]("[12:00:00] [Server thread/INFO]: Disconnecting Bob_1 (/10.0.0.9:5000): "
                          "You are not white-listed on this server!")
    assert app.state.runtime.recent()[0]["name"] == "Bob_1" and app.state.runtime.recent()[0]["rejected"] is True


def test_players_page_renders():
    text = client.get("/dashboard/players").text
    for element_id in ("whitelist-add-form", "whitelist-list", "ops-add-form", "bans-list", "recent-players-list",
                       "players-whitelist-enabled"):
        assert f'id="{element_id}"' in text
    assert 'href="/dashboard/players"' in client.get("/dashboard/overview").text


# ---------- Friend invite and public status page (FEAT-03) ----------
@pytest.fixture
def offline_network(monkeypatch):
    from core import netinfo
    monkeypatch.setattr(netinfo, "public_ip", lambda: "203.0.113.9")
    monkeypatch.setattr(netinfo, "lan_ip", lambda: "192.168.1.50")


def _enable_status_page():
    info = client.post("/api/status-page", auth=AUTH, json={"action": "enable"}).json()
    return info, info["url"].rsplit("/s/", 1)[1]


def test_status_page_is_off_by_default(offline_network):
    assert client.get("/api/status-page", auth=AUTH).json()["enabled"] is False
    assert client.get("/s/anything").status_code == 404
    assert client.get("/s/anything/status.json").status_code == 404
    assert client.get("/api/status-page").status_code == 401


def test_status_page_shows_only_public_fields(offline_network, monkeypatch):
    playit.save_config("very-secret-playit-key", "friends.joinmc.link")
    notifier.save_config({"smtp_password": "smtp-secret-pass", "email_to": "owner@example.com"})
    config_store.save({"motd": "Our World", "max_players": 8, "server_port": 25570})
    info, token = _enable_status_page()
    assert info["enabled"] and info["url_is_lan_only"] and info["url"].startswith("http://192.168.1.50:")
    page = client.get(f"/s/{token}")
    assert page.status_code == 200 and "noindex" in page.headers["x-robots-tag"]
    data = client.get(f"/s/{token}/status.json").json()
    assert data["name"] == "Our World" and data["online"] is False and data["players_max"] == 8
    assert data["address"] == "friends.joinmc.link" and "lan_address" not in data
    everything = page.text + json.dumps(data)
    for secret in ("very-secret-playit-key", "smtp-secret-pass", "owner@example.com", token, "192.168.1.50", "s3cret-pass"):
        assert secret not in everything, secret
    client.post("/api/status-page", auth=AUTH, json={"action": "settings", "show_lan": True, "show_names": False})
    data = client.get(f"/s/{token}/status.json").json()
    assert data["lan_address"] == "192.168.1.50:25570" and "player_names" not in data


def test_status_page_falls_back_to_public_ip(offline_network):
    config_store.save({"server_port": 25565})
    _, token = _enable_status_page()
    assert client.get(f"/s/{token}/status.json").json()["address"] == "203.0.113.9"


def test_regenerating_or_disabling_kills_the_old_link(offline_network):
    _, token = _enable_status_page()
    new_token = client.post("/api/status-page", auth=AUTH, json={"action": "regenerate"}).json()["url"].rsplit("/s/", 1)[1]
    assert new_token != token
    assert client.get(f"/s/{token}").status_code == 404
    assert client.get(f"/s/{new_token}").status_code == 200
    client.post("/api/status-page", auth=AUTH, json={"action": "disable"})
    assert client.get(f"/s/{new_token}").status_code == 404
    assert client.get(f"/s/{new_token}/status.json").status_code == 404


def test_status_page_token_cannot_be_set_through_config():
    response = client.put("/api/config", auth=AUTH, json={"status_page_token": "chosen-by-attacker"})
    assert response.status_code == 422


def test_invite_text_has_address_version_and_mods(offline_network):
    playit.save_config("k", "friends.joinmc.link")
    config_store.save({"loader": "fabric", "version": "1.21.1"})
    mods_dir = os.path.join(common_module._server_dir_for_config(common_module._effective_config()), "mods")
    os.makedirs(mods_dir, exist_ok=True)
    open(os.path.join(mods_dir, "sodium.jar"), "wb").close()
    info, token = _enable_status_page()
    invite = info["invite"]
    assert "Address: friends.joinmc.link" in invite
    assert "Version: Fabric 1.21.1 (mods required" in invite
    assert f"Status page: {info['url']}" in invite
    assert client.get(f"/s/{token}/status.json").json()["mods_required"] is True


def test_invite_card_is_on_overview_and_remote():
    for section in ("overview", "remote"):
        text = client.get(f"/dashboard/{section}").text
        for element_id in ("invite-copy-button", "invite-text", "status-page-enabled", "status-page-regenerate"):
            assert f'id="{element_id}"' in text, (section, element_id)


def test_new_link_email_includes_status_page_link(fake_cloudflared, sent_emails, offline_network):
    _, token = _enable_status_page()
    client.post("/api/remote-dashboard", auth=AUTH, json={"enabled": True})
    assert _wait_until(lambda: any("New dashboard link" in s for s, _ in sent_emails))
    link_mail = next(body for subject, body in sent_emails if "New dashboard link" in subject)
    assert f"https://demo-1.trycloudflare.com/s/{token}" in link_mail
    assert client.get("/api/status-page", auth=AUTH).json()["url"] == f"https://demo-1.trycloudflare.com/s/{token}"


# ---------- Client mod pack export (FEAT-04) ----------
import hashlib  # noqa: E402

from core import modpack  # noqa: E402


@pytest.fixture
def fabric_server(monkeypatch):
    """A Fabric server dir with three jars and a faked Modrinth that knows two of them."""
    config_store.save({"loader": "fabric", "version": "1.21.1", "motd": "Our Pack", "online_mode": False})
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    mods = os.path.join(sdir, "mods")
    os.makedirs(mods, exist_ok=True)
    os.makedirs(os.path.join(sdir, "libraries", "net", "fabricmc", "fabric-loader", "0.16.10"), exist_ok=True)
    jars = {"sodium.jar": b"client and server", "lagfix.jar": b"server only", "homemade.jar": b"not on modrinth"}
    for name, content in jars.items():
        with open(os.path.join(mods, name), "wb") as handle:
            handle.write(content)
    sha1 = {name: hashlib.sha1(content).hexdigest() for name, content in jars.items()}
    calls = []

    def lookup(hashes):
        calls.append(sorted(hashes))
        known = {"sodium.jar": "P1", "lagfix.jar": "P2"}
        return {sha1[name]: {"project_id": pid, "files": [{"hashes": {"sha1": sha1[name]},
                                                           "url": f"https://cdn.modrinth.com/data/{pid}/{name}"}]}
                for name, pid in known.items()}

    monkeypatch.setattr(modpack, "_lookup", lookup)
    monkeypatch.setattr(modpack, "_projects", lambda ids: {
        "P1": {"id": "P1", "title": "Sodium", "client_side": "required", "server_side": "optional"},
        "P2": {"id": "P2", "title": "Lag Fix", "client_side": "unsupported", "server_side": "required"}})
    return {"sdir": sdir, "jars": jars, "calls": calls}


def test_modpack_index_follows_the_mrpack_format(fabric_server):
    summary = client.post("/api/modpack/build", auth=AUTH).json()
    assert summary["modrinth"] == 1 and summary["bundled"] == ["homemade.jar"] and summary["excluded"] == ["Lag Fix"]
    assert summary["filename"] == "Our-Pack-1.21.1.mrpack" and summary["loader_version"] == "0.16.10"
    with zipfile.ZipFile(modpack.pack_path(fabric_server["sdir"])) as pack:
        index = json.loads(pack.read("modrinth.index.json"))
        assert pack.read("overrides/mods/homemade.jar") == b"not on modrinth"
        assert "overrides/mods/lagfix.jar" not in pack.namelist()
    assert index["formatVersion"] == 1 and index["game"] == "minecraft"
    assert index["dependencies"] == {"minecraft": "1.21.1", "fabric-loader": "0.16.10"}
    [entry] = index["files"]
    content = fabric_server["jars"]["sodium.jar"]
    assert entry["path"] == "mods/sodium.jar" and entry["fileSize"] == len(content)
    assert entry["hashes"] == {"sha1": hashlib.sha1(content).hexdigest(), "sha512": hashlib.sha512(content).hexdigest()}
    assert entry["downloads"] == ["https://cdn.modrinth.com/data/P1/sodium.jar"]
    assert entry["env"] == {"client": "required", "server": "optional"}
    download = client.get("/api/modpack/download", auth=AUTH)
    assert download.status_code == 200 and 'filename="Our-Pack-1.21.1.mrpack"' in download.headers["content-disposition"]


def test_modpack_is_cached_until_mods_change(fabric_server):
    first = client.post("/api/modpack/build", auth=AUTH).json()
    second = client.post("/api/modpack/build", auth=AUTH).json()
    assert second["built_at"] == first["built_at"] and len(fabric_server["calls"]) == 1
    with open(os.path.join(fabric_server["sdir"], "mods", "extra.jar"), "wb") as handle:
        handle.write(b"new mod")
    client.post("/api/modpack/build", auth=AUTH)
    assert len(fabric_server["calls"]) == 2


def test_modpack_bundles_everything_when_modrinth_is_down(fabric_server, monkeypatch):
    import requests as requests_lib

    def offline(hashes):
        raise requests_lib.ConnectionError("no internet")

    monkeypatch.setattr(modpack, "_lookup", offline)
    summary = client.post("/api/modpack/build", auth=AUTH).json()
    assert summary["modrinth"] == 0 and len(summary["bundled"]) == 3
    assert any("could not be reached" in warning for warning in summary["warnings"])


def test_modpack_not_offered_for_paper_or_empty_servers():
    config_store.save({"loader": "paper", "version": "1.21.4"})
    assert client.get("/api/modpack", auth=AUTH).json()["available"] is False
    response = client.post("/api/modpack/build", auth=AUTH)
    assert response.status_code == 400 and "without mods" in response.json()["detail"]
    config_store.save({"loader": "fabric", "version": "1.21.1"})
    assert client.post("/api/modpack/build", auth=AUTH).json()["detail"] == "There are no mods on this server yet."


def test_loader_versions_are_detected(tmp_path):
    with zipfile.ZipFile(tmp_path / "server.jar", "w") as jar:
        jar.writestr("install.properties", "fabric-loader-version=0.15.11\ngame-version=1.20.4\n")
    assert modpack.loader_version("fabric", str(tmp_path), "1.20.4") == "0.15.11"
    os.makedirs(tmp_path / "libraries" / "net" / "minecraftforge" / "forge" / "1.20.1-47.3.0")
    assert modpack.loader_version("forge", str(tmp_path), "1.20.1") == "47.3.0"
    os.makedirs(tmp_path / "libraries" / "net" / "neoforged" / "neoforge" / "21.1.73")
    assert modpack.loader_version("neoforge", str(tmp_path), "1.21.1") == "21.1.73"


def test_friends_get_the_pack_from_the_status_page(fabric_server, offline_network):
    info, token = _enable_status_page()
    assert "get the mod pack from the status page" in info["invite"]
    data = client.get(f"/s/{token}/status.json").json()
    assert data["mods_required"] is True and data["modpack_url"] == "modpack.mrpack"
    response = TestClient(app).get(f"/s/{token}/modpack.mrpack")  # signed out
    assert response.status_code == 200
    assert "modrinth.index.json" in zipfile.ZipFile(__import__("io").BytesIO(response.content)).namelist()
    assert client.get("/s/wrong-token/modpack.mrpack").status_code == 404


def test_paper_invite_says_no_mods_needed(offline_network):
    config_store.save({"loader": "paper", "version": "1.21.4"})
    assert "(no mods needed)" in client.get("/api/status-page", auth=AUTH).json()["invite"]


# ---------- Discord webhooks (FEAT-05) ----------
OWNER_HOOK = "https://discord.com/api/webhooks/111/owner-token"
FRIENDS_HOOK = "https://discord.com/api/webhooks/222/friends-token"


@pytest.fixture
def discord_posts(monkeypatch):
    """Capture Discord webhook posts: list of (url, content)."""
    posts: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "send_discord", lambda url, content: posts.append((url, content)))
    monkeypatch.setattr(common_module, "FRIENDS_BATCH_SEC", 0.05)
    return posts


def _hooks(**extra):
    return client.put("/api/notifications", auth=AUTH,
                      json={"discord_owner_url": OWNER_HOOK, "discord_friends_url": FRIENDS_HOOK, **extra})


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


# ---------- Scheduled backups and retention (FEAT-01) ----------
import datetime  # noqa: E402

from core import backups as backups_core  # noqa: E402

webapp_module_backups = importlib.import_module("webapp.routes.backups")


def _world_dir(folder="world"):
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    os.makedirs(os.path.join(sdir, folder), exist_ok=True)
    with open(os.path.join(sdir, folder, "level.dat"), "wb") as handle:
        handle.write(b"level")
    return sdir


def test_backup_names_old_and_new_parse():
    assert backups_core.parse("world-backup-20260101-000000.zip")["label"] == "manual"
    old = backups_core.parse("world-backup-my-world-20261022-101010.zip")
    assert old["folder"] == "my-world" and old["label"] == "manual"
    new = backups_core.parse("world-backup-my-world-20261022-101010-auto-pre-update.zip")
    assert new["folder"] == "my-world" and new["label"] == "auto-pre-update"
    assert new["created"] == datetime.datetime(2026, 10, 22, 10, 10, 10)
    assert backups_core.parse("world-backup-evil.zip") is None


def test_retention_keeps_recent_and_one_per_day_and_never_manual():
    now = datetime.datetime(2026, 10, 22, 12, 0, 0)

    def item(name, hours_ago, label="auto"):
        return {"name": name, "folder": "world", "label": label, "created": now - datetime.timedelta(hours=hours_ago)}

    items = [item(f"a{h}", h) for h in (1, 2, 3, 4, 30, 31, 55, 80, 200)] + [item("m", 500, "manual")]
    doomed = {entry["name"] for entry in backups_core.to_prune(items, keep_recent=3, keep_daily=3, now=now)}
    # kept: newest 3 (a1 a2 a3) + newest of each of the last 3 days (a1 today, a30 yesterday, a55 two days ago)
    assert doomed == {"a4", "a31", "a80", "a200"}
    assert "m" in {entry["name"] for entry in backups_core.to_prune(items, 3, 3, prune_manual=True, now=now)}


def test_schedule_backs_up_only_a_changing_world(fake_server):
    sdir = _world_dir()
    services = app.state.services
    app.state.runtime.backup_dirty = False
    now = time.time()
    services.backup_tick(now)
    assert backups_core.list_backups(sdir) == []  # stopped and unchanged: nothing to do
    _start()
    fake_server[0].send = lambda command: (fake_server[0].sent.append(command),
                                           command == "save-all flush" and services.append_console_line(
                                               "[12:00:00] [Server thread/INFO]: Saved the game"))
    services.backup_tick(now)
    [first] = backups_core.list_backups(sdir)
    assert first["label"] == "auto"
    assert fake_server[0].sent == ["save-off", "save-all flush", "save-on"]
    services.backup_tick(now + 60)
    assert len(backups_core.list_backups(sdir)) == 1  # not due yet
    services.backup_tick(now + 6 * 3600 + 5)
    assert len(backups_core.list_backups(sdir)) == 2


def test_manual_backup_is_labelled_and_survives_pruning():
    sdir = _world_dir()
    config_store.save({"backup_keep_recent": 1, "backup_keep_daily": 0})
    manual = client.post("/api/server/backup", auth=AUTH).json()
    assert manual["ok"] and manual["archive"].endswith("-manual.zip")
    services = app.state.services
    services.safety_backup("update")
    time.sleep(1.1)
    services.safety_backup("update")
    names = [item["name"] for item in backups_core.list_backups(sdir)]
    assert sum(name.endswith("-manual.zip") for name in names) == 1
    assert sum("auto-pre-update" in name for name in names) == 1  # keep_recent=1 pruned the older one
    listing = client.get("/api/server/backups", auth=AUTH).json()
    assert listing["schedule"]["keep_recent"] == 1 and listing["total_size"] > 0
    assert {item["label"] for item in listing["items"]} == {"manual", "auto-pre-update"}


def test_low_disk_skips_the_backup_and_alerts(monkeypatch, sent_emails):
    _world_dir()
    client.post("/api/server/backup", auth=AUTH)
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(webapp_module_backups.shutil, "disk_usage", lambda path: usage(10, 10, 0))
    response = client.post("/api/server/backup", auth=AUTH)
    assert response.status_code == 500 and "Not enough free space" in response.json()["detail"]
    assert _wait_until(lambda: any("Backup failed" in subject for subject, _ in sent_emails))


def test_deleting_a_world_makes_a_safety_backup_first(monkeypatch):
    sdir = _world_dir("old-world")
    _world_dir("world")
    response = client.delete("/api/worlds/old-world", auth=AUTH)
    assert response.status_code == 200
    assert [item["label"] for item in backups_core.list_backups(sdir)] == ["auto-pre-delete"]
    _world_dir("other")
    monkeypatch.setattr(backups_core, "create", lambda *args: (_ for _ in ()).throw(OSError(28, "disk full")))
    response = client.delete("/api/worlds/other", auth=AUTH)
    assert response.status_code == 500 and os.path.isdir(os.path.join(sdir, "other"))


def test_backup_settings_are_validated():
    assert client.put("/api/config", auth=AUTH, json={"backup_interval_hours": 500}).status_code == 422
    assert client.put("/api/config", auth=AUTH, json={"backup_keep_recent": 0}).status_code == 422
    assert client.put("/api/config", auth=AUTH, json={"backup_interval_hours": 12}).status_code == 200


def test_dashboard_scripts_do_not_redefine_each_others_globals():
    # The scripts share one global scope: a second top-level function/const with the same name
    # silently replaces the first (as nearly happened with formatBytes).
    names = re.findall(r"(?m)^(?:async function|function|const|let)\s+([A-Za-z_$][\w$]*)", _dashboard_js())
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], duplicates


# ---------- Scheduled restarts, idle stop, quiet hours (FEAT-06) ----------
def _at(hhmm, day=22):
    hours, minutes = map(int, hhmm.split(":"))
    return datetime.datetime(2026, 10, day, hours, minutes, 30)


@pytest.fixture
def schedule_runtime():
    runtime = app.state.runtime
    runtime.daily_restart_done = ""
    runtime.restart_warnings_sent.clear()
    runtime.stopped_reason = ""
    runtime.online_players.clear()
    yield runtime
    runtime.online_players.clear()


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


# ---------- Crash diagnosis and log viewer (FEAT-08) ----------
import gzip  # noqa: E402

from core import diagnose  # noqa: E402

LOG_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "logs")


def _diagnose_fixture(name):
    with open(os.path.join(LOG_FIXTURES, name), encoding="utf-8") as handle:
        return diagnose.diagnose(handle.read().splitlines())


def test_diagnosis_recognises_real_failures():
    fabric = _diagnose_fixture("fabric_missing_dependency.log")
    assert [(d["title"], d["action"]) for d in fabric if d["code"] == "missing_dependency"] == [
        ("Sodium Extra needs Sodium", {"kind": "install_mod", "slug": "sodium"}),
        ("Mod Menu needs fabric-api", {"kind": "install_mod", "slug": "fabric-api"})]
    assert fabric[-1]["code"] == "incompatible_mods"
    forge = _diagnose_fixture("forge_missing_dependency.log")
    assert forge[0]["title"] == "roughlyenoughitems needs architectury" and "not installed" in forge[0]["message"]
    assert "47.0.19 is installed" in forge[1]["message"]
    java = _diagnose_fixture("java_version.log")[0]
    assert java["code"] == "wrong_java" and "needs Java 21, but Java 17" in java["message"]
    assert _diagnose_fixture("out_of_memory.log")[0]["action"] == {"kind": "open_setting", "page": "setup",
                                                                   "field": "ram-input"}
    port = _diagnose_fixture("port_in_use.log")[0]
    assert port["title"] == "Port 25565 is already in use" and port["action"] == {"kind": "kill_process", "port": 25565}
    client_only = _diagnose_fixture("client_only_mod.log")[0]
    assert client_only["action"] == {"kind": "remove_mod", "file": "oculus-mc1.20.1-1.6.9.jar"}
    assert _diagnose_fixture("eula.log")[0]["action"] == {"kind": "accept_eula"}
    assert _diagnose_fixture("world_locked.log")[0]["code"] == "world_locked"
    assert diagnose.diagnose(["[12:00:00] [Server thread/INFO]: Done (3.2s)!"]) == []


def test_crash_is_diagnosed_and_explained_in_the_alert(fake_server, sent_emails):
    config_store.save({"auto_restart": False})  # keep the crashed state to inspect it
    _start()
    lines = open(os.path.join(LOG_FIXTURES, "fabric_missing_dependency.log"), encoding="utf-8").read().splitlines()
    for line in lines:
        app.state.services.append_console_line(line)
    fake_server[0].exit(1)
    diagnosis_items = client.get("/api/server/diagnosis", auth=AUTH).json()["items"]
    assert diagnosis_items[0]["title"] == "Sodium Extra needs Sodium"
    assert client.get("/api/status", auth=AUTH).json()["diagnosis_at"] > 0
    assert _wait_until(lambda: any("Likely cause: Sodium Extra needs Sodium" in body for _, body in sent_emails))
    _start()  # a new start clears the old diagnosis
    assert client.get("/api/server/diagnosis", auth=AUTH).json()["items"] == []


def test_diagnosis_fixes_eula_and_only_kills_java(monkeypatch):
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    assert client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "accept_eula"}).status_code == 200
    assert "eula=true" in open(os.path.join(sdir, "eula.txt"), encoding="utf-8").read()
    terminated = []

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return {111: "java.exe", 222: "chrome.exe"}[self.pid]

        def terminate(self):
            terminated.append(self.pid)

    class Conn:
        def __init__(self, port, pid):
            self.laddr, self.status, self.pid = type("A", (), {"port": port})(), "LISTEN", pid

    fake_psutil = type("P", (), {"Error": Exception, "Process": FakeProcess,
                                 "net_connections": staticmethod(lambda kind: [Conn(25565, 111), Conn(8080, 222)])})
    monkeypatch.setattr(common_module, "psutil", fake_psutil)
    assert client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "kill_process", "port": 25565}).status_code == 200
    refused = client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "kill_process", "port": 8080})
    assert refused.status_code == 409 and "chrome.exe" in refused.json()["detail"]
    assert terminated == [111]
    assert client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "kill_process", "port": 1}).status_code == 404


def test_log_viewer_lists_reads_gz_and_refuses_traversal():
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    os.makedirs(os.path.join(sdir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(sdir, "crash-reports"), exist_ok=True)
    with open(os.path.join(sdir, "logs", "latest.log"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(f"line {i}" for i in range(2500)))
    with gzip.open(os.path.join(sdir, "logs", "2026-10-21-1.log.gz"), "wt", encoding="utf-8") as handle:
        handle.write("old line\n")
    with open(os.path.join(sdir, "crash-reports", "crash-2026-10-22_12.00.00-server.txt"), "w", encoding="utf-8") as handle:
        handle.write("---- Minecraft Crash Report ----\n")
    files = client.get("/api/server/logs", auth=AUTH).json()["files"]
    assert {(f["folder"], f["name"]) for f in files} == {("logs", "latest.log"), ("logs", "2026-10-21-1.log.gz"),
                                                         ("crash-reports", "crash-2026-10-22_12.00.00-server.txt")}
    latest = client.get("/api/server/logs/logs/latest.log", auth=AUTH).json()
    assert latest["truncated"] and len(latest["lines"]) == 2000 and latest["lines"][-1] == "line 2499"
    assert client.get("/api/server/logs/logs/2026-10-21-1.log.gz", auth=AUTH).json()["lines"] == ["old line"]
    download = client.get("/api/server/logs/logs/latest.log?download=true", auth=AUTH)
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    for bad in ("/api/server/logs/logs/..%2F..%2Flauncher_config.json", "/api/server/logs/world/level.dat",
                "/api/server/logs/logs/eula.txt.bak"):
        assert client.get(bad, auth=AUTH).status_code in (400, 404), bad
    assert client.get("/api/server/logs/logs/missing.log", auth=AUTH).status_code == 404


def test_diagnosis_and_logs_cards_render():
    console = client.get("/dashboard/console").text
    for element_id in ("diagnosis", "diagnosis-list", "log-list", "log-view"):
        assert f'id="{element_id}"' in console
    assert 'id="diagnosis"' in client.get("/dashboard/overview").text


# ---------- Mod update checker (FEAT-07) ----------
from core import mod_updates  # noqa: E402


@pytest.fixture
def outdated_mods(monkeypatch):
    """A Fabric server with an outdated recorded mod, an outdated uploaded jar and an unknown jar."""
    config_store.save({"loader": "fabric", "version": "1.21.1", "online_mode": False})
    sdir = _world_dir()
    mods = os.path.join(sdir, "mods")
    os.makedirs(mods, exist_ok=True)
    jars = {"sodium-0.5.jar": b"sodium old", "lithium-0.10.jar": b"lithium old", "homemade.jar": b"mine"}
    for name, content in jars.items():
        with open(os.path.join(mods, name), "wb") as handle:
            handle.write(content)
    from core import modrinth
    modrinth._save_record(mods, {"P1": {"slug": "sodium", "title": "Sodium", "filename": "sodium-0.5.jar", "version": "0.5"}})
    new_content = {"sodium-0.6.jar": b"sodium new", "lithium-0.11.jar": b"lithium new"}

    def version(pid, number, filename, deps=()):
        content = new_content[filename]
        return {"project_id": pid, "version_number": number, "changelog": f"## {number}\n- fixes",
                "dependencies": [{"project_id": d, "dependency_type": "required"} for d in deps],
                "files": [{"primary": True, "filename": filename, "url": f"https://cdn.modrinth.com/{filename}",
                           "size": len(content), "hashes": {"sha1": hashlib.sha1(content).hexdigest(),
                                                            "sha512": hashlib.sha512(content).hexdigest()}}]}

    requests_made = []

    class Reply:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def fake_post(url, headers, timeout, json):
        requests_made.append((url, json))
        sha = {hashlib.sha1(c).hexdigest(): n for n, c in jars.items()}
        answer = {}
        for h in json["hashes"]:
            if sha.get(h) == "sodium-0.5.jar":
                answer[h] = version("P1", "0.6", "sodium-0.6.jar", deps=["P9"])
            elif sha.get(h) == "lithium-0.10.jar":
                answer[h] = version("P2", "0.11", "lithium-0.11.jar")
        return Reply(answer)

    def fake_download(url, dest, log=print, progress=None):
        with open(dest, "wb") as handle:
            handle.write(new_content[url.rsplit("/", 1)[1]])
        return dest

    installed_deps = []
    monkeypatch.setattr(mod_updates.requests, "post", fake_post)
    monkeypatch.setattr(mod_updates, "download", fake_download)
    monkeypatch.setattr(modrinth, "install", lambda ref, loader, gv, dest, log=print: installed_deps.append(ref))
    return {"mods": mods, "requests": requests_made, "deps": installed_deps, "sdir": sdir}


def test_check_finds_updates_with_one_request(outdated_mods):
    result = client.post("/api/mods/updates/check", auth=AUTH).json()
    assert [(u["title"], u["current"], u["latest"]) for u in result["updates"]] == [
        ("lithium-0.10", "lithium-0.10", "0.11"), ("Sodium", "0.5", "0.6")]
    assert "fixes" in result["updates"][1]["changelog"] and "url" not in result["updates"][0]
    [(url, body)] = outdated_mods["requests"]
    assert url.endswith("/version_files/update") and len(body["hashes"]) == 3
    assert body["loaders"] == ["fabric"] and body["game_versions"] == ["1.21.1"]


def test_update_replaces_jar_keeps_old_and_installs_dependency(outdated_mods):
    client.post("/api/mods/updates/check", auth=AUTH)
    result = client.post("/api/mods/updates/apply", auth=AUTH, json={"files": ["sodium-0.5.jar"]}).json()
    mods = outdated_mods["mods"]
    assert result["updated"] == ["Sodium"] and result["pack_changed"] is True
    assert open(os.path.join(mods, "sodium-0.6.jar"), "rb").read() == b"sodium new"
    assert os.path.exists(os.path.join(mods, "sodium-0.5.jar.old"))
    assert not os.path.exists(os.path.join(mods, "sodium-0.5.jar"))
    from core import modrinth
    assert modrinth.load_record(mods)["P1"]["version"] == "0.6"
    assert outdated_mods["deps"] == ["P9"]
    assert [u["file"] for u in result["updates"]] == ["lithium-0.10.jar"] and result["rollback_available"]
    assert [b["label"] for b in backups_core.list_backups(outdated_mods["sdir"])] == ["auto-pre-mod-update"]
    undo = client.post("/api/mods/updates/rollback", auth=AUTH).json()
    assert "Restored 1" in undo["message"]
    assert open(os.path.join(mods, "sodium-0.5.jar"), "rb").read() == b"sodium old"
    assert not os.path.exists(os.path.join(mods, "sodium-0.6.jar"))
    assert modrinth.load_record(mods)["P1"]["filename"] == "sodium-0.5.jar"


def test_corrupted_download_leaves_the_old_jar(outdated_mods, monkeypatch):
    client.post("/api/mods/updates/check", auth=AUTH)
    monkeypatch.setattr(mod_updates, "download",
                        lambda url, dest, log=print, progress=None: open(dest, "wb").write(b"tampered") and dest)
    result = client.post("/api/mods/updates/apply", auth=AUTH, json={}).json()
    assert result["updated"] == [] and len(result["failed"]) == 2 and "corrupted" in result["message"]
    assert sorted(os.listdir(outdated_mods["mods"])) == [".ez-modrinth.json", "homemade.jar", "lithium-0.10.jar",
                                                         "sodium-0.5.jar"]


def test_updates_need_a_stopped_server(outdated_mods, fake_server):
    client.post("/api/mods/updates/check", auth=AUTH)
    _start()
    assert client.post("/api/mods/updates/apply", auth=AUTH, json={}).status_code == 409
    assert client.post("/api/mods/updates/rollback", auth=AUTH).status_code == 409


def test_old_jars_are_removed_after_the_server_ran_fine(outdated_mods, fake_server):
    client.post("/api/mods/updates/check", auth=AUTH)
    client.post("/api/mods/updates/apply", auth=AUTH, json={})
    _start()
    tick = app.state.services.scheduler_tick
    started = app.state.runtime.last_start_at
    tick(datetime.datetime.fromtimestamp(started + 60))
    assert mod_updates.old_jars(outdated_mods["mods"]) != []
    tick(datetime.datetime.fromtimestamp(started + common_module.OLD_MODS_KEEP_SEC + 1))
    assert mod_updates.old_jars(outdated_mods["mods"]) == []


def test_vanilla_has_no_mod_updates():
    assert client.post("/api/mods/updates/check", auth=AUTH).status_code == 400


# ---------- GET /api/status is a pure read (FIX-06) ----------
def test_status_reflects_config_without_writing_runtime(offline_network):
    runtime = app.state.runtime
    runtime.loader, runtime.version, runtime.lan_address, runtime.public_address = "marker", "0", "unset", None
    config_store.save({"loader": "paper", "version": "1.21.4"})
    first = client.get("/api/status", auth=AUTH).json()
    config_store.save({"loader": "fabric", "version": "1.21.1"})
    second = client.get("/api/status", auth=AUTH).json()
    assert (first["loader"], first["version"]) == ("paper", "1.21.4")
    assert (second["loader"], second["version"]) == ("fabric", "1.21.1")
    assert second["lan_address"] == "192.168.1.50" and second["public_address"] == "203.0.113.9"
    assert second["playit"]["state"] in {"not-configured", "configured", "stopped", "running"}
    assert (runtime.loader, runtime.version, runtime.lan_address, runtime.public_address) == ("marker", "0", "unset", None)


def test_status_handler_has_no_runtime_assignments():
    source = open(os.path.join(os.path.dirname(webapp_module.__file__), "routes", "system.py"), encoding="utf-8").read()
    handler = source[source.index("def status_payload"):source.index('@app.get("/api/console")')]
    assert not re.search(r"runtime\.\w+\s*=[^=]", handler)


# ---------- Live console and chat (FEAT-11) ----------
def test_console_since_returns_only_newer_lines():
    manager = app.state.console_manager
    manager.publish("first line")
    mark = client.get("/api/console", auth=AUTH).json()["last"]
    manager.publish("second line")
    manager.publish("third line")
    newer = client.get(f"/api/console?since={mark}", auth=AUTH).json()
    assert newer["lines"] == ["second line", "third line"] and newer["last"] == mark + 2
    assert client.get(f"/api/console?since={newer['last']}", auth=AUTH).json()["lines"] == []
    assert client.get("/api/console?since=0", auth=AUTH).json()["lines"][-1] == "third line"


def test_chat_uses_tellraw_json_and_validates(fake_server):
    assert client.post("/api/server/chat", auth=AUTH, json={"message": "hi"}).status_code == 409
    _start()
    tricky = 'hi "all" \ ok'
    assert client.post("/api/server/chat", auth=AUTH, json={"message": tricky}).status_code == 200
    command = fake_server[0].sent[-1]
    assert command.startswith("tellraw @a ")
    assert json.loads(command[len("tellraw @a "):])[-1] == {"text": tricky}
    assert json.loads(command[len("tellraw @a "):])[2] == {"text": "admin: ", "color": "aqua"}
    for bad in ("line\nbreak", "", "x" * 201):
        assert client.post("/api/server/chat", auth=AUTH, json={"message": bad}).status_code == 400
    assert any(line == f"[chat] admin: {tricky}" for line in app.state.console_manager.history)


def test_console_page_has_filters_and_overview_has_chat():
    console = client.get("/dashboard/console").text
    assert 'id="console-filter"' in console and 'id="console-search"' in console
    assert 'id="chat-form"' in client.get("/dashboard/overview").text
