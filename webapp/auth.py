from __future__ import annotations

import base64
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, status
from starlette.requests import HTTPConnection

SESSION_COOKIE = "ezmc_session"
# Sliding expiry: every authenticated request pushes it back.
SESSION_TTL_SEC = 7 * 24 * 3600
# Per client IP: LOCKOUT_FAILURES wrong logins within FAILURE_WINDOW_SEC lock that IP for LOCKOUT_SEC.
LOCKOUT_FAILURES = 5
FAILURE_WINDOW_SEC = 15 * 60
LOCKOUT_SEC = 15 * 60
# Across all IPs, so rotating addresses can't brute-force either.
GLOBAL_FAILURE_LIMIT = 30
WEAK_PASSWORD_LENGTH = 12
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def validate_auth_settings() -> None:
    username = os.getenv("EZ_WEBAPP_USERNAME", "").strip()
    password = os.getenv("EZ_WEBAPP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            "Authentication is required. Set EZ_WEBAPP_USERNAME and EZ_WEBAPP_PASSWORD before starting the web app."
        )


def get_expected_credentials() -> tuple[str, str]:
    username = os.getenv("EZ_WEBAPP_USERNAME", "").strip()
    password = os.getenv("EZ_WEBAPP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            "Authentication is required. Set EZ_WEBAPP_USERNAME and EZ_WEBAPP_PASSWORD before starting the web app."
        )
    return username, password


def password_is_weak() -> bool:
    return len(get_expected_credentials()[1]) < WEAK_PASSWORD_LENGTH


def credentials_match(username: str, password: str) -> bool:
    """Constant-time comparison so response timing does not leak the expected credentials."""
    expected_username, expected_password = get_expected_credentials()
    username_ok = secrets.compare_digest(username.encode("utf-8"), expected_username.encode("utf-8"))
    password_ok = secrets.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8"))
    return username_ok and password_ok


class SessionStore:
    """In-memory login sessions: token -> (username, expires_at). A restart logs everyone out."""

    def __init__(self, ttl: float = SESSION_TTL_SEC, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self.clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[str, float]] = {}

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._sessions[token] = (username, self.clock() + self.ttl)
        return token

    def get(self, token: str | None) -> str | None:
        """Username for a live session (and extend it), else None."""
        if not token:
            return None
        with self._lock:
            self._prune()
            entry = self._sessions.get(token)
            if entry is None:
                return None
            self._sessions[token] = (entry[0], self.clock() + self.ttl)
            return entry[0]

    def delete(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _prune(self) -> None:
        now = self.clock()
        for token in [token for token, (_, expires) in self._sessions.items() if expires <= now]:
            del self._sessions[token]


class LoginLimiter:
    """Counts failed logins per client IP and overall; locks out after too many."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}
        self._all_failures: deque[float] = deque()
        self._locked_until: dict[str, float] = {}
        self._global_locked_until = 0.0

    def retry_after(self, ip: str) -> int:
        """Seconds until `ip` may try again; 0 when allowed."""
        with self._lock:
            now = self.clock()
            until = max(self._locked_until.get(ip, 0.0), self._global_locked_until)
            return max(0, int(until - now + 0.999))

    def record_failure(self, ip: str) -> bool:
        """Count a wrong login. True when this failure starts a new lockout."""
        with self._lock:
            now = self.clock()
            self._prune(now)
            failures = self._failures.setdefault(ip, deque())
            failures.append(now)
            self._all_failures.append(now)
            started = False
            if len(failures) >= LOCKOUT_FAILURES and self._locked_until.get(ip, 0.0) <= now:
                self._locked_until[ip] = now + LOCKOUT_SEC
                failures.clear()
                started = True
            if len(self._all_failures) >= GLOBAL_FAILURE_LIMIT and self._global_locked_until <= now:
                self._global_locked_until = now + LOCKOUT_SEC
                self._all_failures.clear()
                started = True
            return started

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
            self._all_failures.clear()
            self._locked_until.clear()
            self._global_locked_until = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - FAILURE_WINDOW_SEC
        for ip in list(self._failures):
            failures = self._failures[ip]
            while failures and failures[0] <= cutoff:
                failures.popleft()
            if not failures:
                del self._failures[ip]
        while self._all_failures and self._all_failures[0] <= cutoff:
            self._all_failures.popleft()
        for ip in [ip for ip, until in self._locked_until.items() if until <= now]:
            del self._locked_until[ip]


def client_ip(conn: HTTPConnection) -> str:
    """The caller's IP. CF-Connecting-IP is trusted only from cloudflared, which runs on this machine."""
    host = conn.client.host if conn.client else ""
    forwarded = conn.headers.get("cf-connecting-ip", "").strip()
    if forwarded and host in _LOOPBACK:
        return forwarded
    return host or "unknown"


def is_https(conn: HTTPConnection) -> bool:
    return conn.headers.get("x-forwarded-proto", "").lower() == "https" or "cf-connecting-ip" in conn.headers


def locked_out_error(retry_after: int) -> HTTPException:
    minutes = max(1, (retry_after + 59) // 60)
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many wrong passwords. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(retry_after)},
    )


def check_login(conn: HTTPConnection, limiter: LoginLimiter, username: str, password: str,
                on_lockout: Callable[[str], None], basic: bool = False) -> None:
    """Raise 429 while locked out, 401 on wrong credentials (counted towards the lockout).

    Only Basic-auth callers get a `WWW-Authenticate: Basic` challenge: on the login form it would
    make the browser hold the request and open its own password dialog.
    """
    ip = client_ip(conn)
    retry_after = limiter.retry_after(ip)
    if retry_after:
        raise locked_out_error(retry_after)
    if credentials_match(username, password):
        limiter.record_success(ip)
        return
    if limiter.record_failure(ip):
        on_lockout(ip)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Wrong username or password.",
        headers={"WWW-Authenticate": "Basic"} if basic else None,
    )


def _basic_credentials(auth_header: str) -> tuple[str, str]:
    scheme, _, encoded = auth_header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed.") from None
    username, separator, password = decoded.partition(":")
    if separator != ":":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed.")
    return username, password


def _check_same_origin(conn: HTTPConnection) -> None:
    """CSRF guard for cookie sessions: state changes (and WebSockets) must come from this origin;
    HTTP state changes must also be JSON/multipart, which a plain cross-site form cannot send."""
    is_websocket = conn.scope["type"] == "websocket"
    if not is_websocket and conn.scope.get("method", "GET") in _SAFE_METHODS:
        return
    origin = conn.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != conn.headers.get("host", ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site request refused.")
    if is_websocket:
        return
    content_type = conn.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in ("application/json", "multipart/form-data"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requests must send JSON.")


def authenticate(conn: HTTPConnection, sessions: SessionStore, limiter: LoginLimiter,
                 on_lockout: Callable[[str], None]) -> str:
    """Accept a session cookie (browser) or HTTP Basic credentials (scripts). Returns the username."""
    username = sessions.get(conn.cookies.get(SESSION_COOKIE))
    if username is not None:
        _check_same_origin(conn)
        return username
    auth_header = conn.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    username, password = _basic_credentials(auth_header)
    check_login(conn, limiter, username, password, on_lockout, basic=True)
    return username
