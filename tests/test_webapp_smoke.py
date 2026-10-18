import os

os.environ["MZ_WEBAPP_USERNAME"] = "admin"
os.environ["MZ_WEBAPP_PASSWORD"] = "s3cret-pass"

from fastapi.testclient import TestClient

from webapp.app import create_app

app = create_app()
client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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


def test_versions_endpoint_accepts_known_loader():
    response = client.get("/api/versions/vanilla", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "loader" in data
    assert "versions" in data
    assert data["loader"] == "vanilla"
