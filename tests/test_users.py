"""Friend accounts with limited permissions (FEAT-10)."""
from support import *  # noqa: F401,F403

from core import users

# Routes a friend may use; every other /api route must answer 403.
FRIEND_ROUTES = {
    ("GET", "/api/session"), ("GET", "/api/status"), ("POST", "/api/server/start"), ("POST", "/api/server/chat"),
    ("POST", "/api/server/backup"), ("POST", "/api/server/stop"), ("POST", "/api/server/restart"),
    ("GET", "/api/system/metrics"), ("GET", "/api/system"), ("GET", "/api/stats"), ("GET", "/api/metrics"),
    ("GET", "/metrics"),
}
NO_AUTH = {("POST", "/api/login"), ("POST", "/api/logout")}


def _friend(name="alex"):
    password = users.set_password(name)
    browser = TestClient(app)
    assert browser.post("/api/login", json={"username": name, "password": password}).json()["role"] == "friend"
    return browser, password


def test_friend_gets_403_on_every_owner_route():
    browser, _ = _friend()
    checked = 0
    for route in app.routes:
        for method in sorted(getattr(route, "methods", None) or []):
            key = (method, route.path)
            if method == "HEAD" or not route.path.startswith(("/api", "/metrics")) or key in FRIEND_ROUTES | NO_AUTH:
                continue
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            response = browser.request(method, path, json={} if method != "GET" else None)
            assert response.status_code == 403, (method, route.path, response.status_code)
            checked += 1
    assert checked > 60


def test_friend_can_start_chat_back_up_but_not_stop_by_default(fake_server):
    browser, _ = _friend()
    assert browser.get("/api/session").json() == {"username": "alex", "role": "friend", "can_stop": False}
    response = browser.post("/api/server/start", json={"server_port": 30000})  # settings in the body are ignored
    assert response.status_code == 202
    _wait_idle()
    assert common_module._effective_config()["server_port"] == 25565
    assert browser.post("/api/server/chat", json={"message": "hi"}).status_code == 200
    assert browser.post("/api/server/stop", json={}).status_code == 403
    assert any(line == "[dashboard] alex started the server" for line in app.state.console_manager.history)
    config_store.save({"friends_can_stop": True})
    assert browser.post("/api/server/stop", json={}).status_code == 200


def test_owner_manages_friends_and_removal_signs_them_out():
    created = client.post("/api/users", auth=AUTH, json={"username": "sam"}).json()
    assert created["username"] == "sam" and len(created["password"]) == 12
    assert [u["username"] for u in created["users"]] == ["sam"]
    friend = TestClient(app)
    friend.post("/api/login", json={"username": "sam", "password": created["password"]})
    assert friend.get("/api/status").status_code == 200
    reset = client.post("/api/users/sam/reset", auth=AUTH).json()
    assert reset["password"] != created["password"]
    assert friend.get("/api/status").status_code == 401  # signed out by the reset
    assert TestClient(app).post("/api/login", json={"username": "sam", "password": created["password"]}).status_code == 401
    friend.post("/api/login", json={"username": "sam", "password": reset["password"]})
    assert client.delete("/api/users/sam", auth=AUTH).status_code == 200
    assert friend.get("/api/status").status_code == 401
    for bad in ("admin", "x", "has space", "../etc"):
        assert client.post("/api/users", auth=AUTH, json={"username": bad}).status_code == 400, bad


def test_owner_account_always_works_and_friends_cant_use_the_console_socket():
    users.set_password("admin2", "friendpassword")
    assert client.get("/api/users", auth=AUTH).status_code == 200  # the env account is the owner
    friend, _ = _friend()
    try:
        with friend.websocket_connect("/ws/console"):
            pass
    except Exception:
        pass
    else:
        raise AssertionError("A friend must not get the console WebSocket.")
    stored = json.load(open(os.path.join(paths.DATA_DIR, "users.json"), encoding="utf-8"))
    assert "friendpassword" not in json.dumps(stored) and stored["admin2"]["salt"]


def test_friend_pages_hide_owner_controls():
    text = client.get("/dashboard/overview").text
    assert 'href="/dashboard/setup" class="nav-link" data-owner-only' in text
    assert 'id="invite" class="card" data-owner-only' in text and "data-stop-control" in text
    assert 'id="user-add-form"' in client.get("/dashboard/users").text
