from fastapi.testclient import TestClient

from webapp.app import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_endpoint_returns_state():
    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert "state" in payload
    assert "loader" in payload
    assert "version" in payload


def test_versions_endpoint_accepts_known_loader():
    response = client.get("/api/versions/vanilla")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "loader" in data
    assert "versions" in data
    assert data["loader"] == "vanilla"
