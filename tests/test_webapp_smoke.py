import importlib
import os

os.environ["EZ_WEBAPP_USERNAME"] = "admin"
os.environ["EZ_WEBAPP_PASSWORD"] = "s3cret-pass"

from fastapi.testclient import TestClient

from webapp.app import create_app

webapp_module = importlib.import_module("webapp.app")

app = create_app()
client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_serves_html():
    response = client.get("/", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "EZ Minecraft Server" in response.text
    assert "Dashboard" in response.text


def test_dashboard_uses_runtime_auth_defaults(monkeypatch):
    monkeypatch.setenv("EZ_WEBAPP_USERNAME", "admin")
    monkeypatch.setenv("EZ_WEBAPP_PASSWORD", "admin")
    response = client.get("/")
    assert response.status_code == 200
    assert 'value="admin"' in response.text
    assert 'name="password" type="password" value="admin"' in response.text
    assert "join('\\\\n')" in response.text
    assert "join('\\\\n')" in response.text


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


def test_playit_status_endpoint_reports_configuration():
    response = client.get("/api/playit/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "configured" in payload
    assert "state" in payload
    assert "address" in payload


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

    def fake_prepare(loader, version, target_dir, java_exe, log=print, progress=None):
        if progress:
            progress(50, 100)
        return None

    monkeypatch.setattr(webapp_module, "_server_is_installed", lambda target_dir, loader: False)
    monkeypatch.setattr(webapp_module.downloaders, "prepare", fake_prepare)
    monkeypatch.setattr(webapp_module.java_manager, "ensure_java", lambda required: "java")
    monkeypatch.setattr(webapp_module.downloaders, "launch_args", lambda *args, **kwargs: ["java", "-jar", "server.jar"])
    monkeypatch.setattr(webapp_module, "ServerProcess", DummyProcess)

    response = client.post("/api/server/start", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Server started."
    assert app.state.runtime.install_status == "ready"
    assert app.state.runtime.install_progress >= 0


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
