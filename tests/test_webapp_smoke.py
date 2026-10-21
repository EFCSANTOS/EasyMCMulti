import importlib
import os
import re
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


def test_dashboard_mentions_playit_support():
    response = client.get("/", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "PlayIt" in response.text


def test_dashboard_mentions_legacy_management_controls():
    response = client.get("/", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "Restart server" in response.text
    assert "Backup world" in response.text
    assert "Mods / Plugins" in response.text
    assert "Admin commands" in response.text


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


def test_playit_start_route_starts_the_tunnel(monkeypatch):
    monkeypatch.setattr(webapp_module.playit, "load_config", lambda: {"secret": "demo-secret", "address": "demo.example"})
    monkeypatch.setattr(webapp_module.playit, "ensure_agent", lambda log=print: "agent.exe")
    monkeypatch.setattr(webapp_module.playit, "ensure_tunnel", lambda secret, local_port, log, timeout_sec=120: "demo.example:25565")

    class DummyAgent:
        def __init__(self, on_output=None, on_exit=None):
            self.on_output = on_output
            self.on_exit = on_exit

        def is_running(self):
            return False

        def start(self, secret):
            self.secret = secret

        def stop(self):
            return None

    monkeypatch.setattr(webapp_module.playit, "PlayitAgent", DummyAgent)

    response = client.post("/api/playit/start", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "running"
    assert payload["address"] == "demo.example:25565"


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
