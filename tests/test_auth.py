"""Login sessions, lockout, Basic auth and CSRF (FIX-01)."""
from support import *  # noqa: F401,F403


def test_status_requires_authentication():
    response = client.get("/api/status")
    assert response.status_code == 401


def test_websocket_console_requires_authentication():
    try:
        with client.websocket_connect("/ws/console"):
            pass
    except Exception:
        return
    raise AssertionError("Unauthenticated WebSocket access should be rejected.")


def test_wrong_password_is_rejected():
    response = client.get("/api/status", auth=("admin", "wrong"))
    assert response.status_code == 401


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
