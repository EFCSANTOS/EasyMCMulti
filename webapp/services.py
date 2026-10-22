"""Shared services for the routers: console, alerts, login state and the auth dependency.

`Services` is a plain namespace. create_app() fills the basics here, then each router's
register(app, s) adds the helpers other routers need (e.g. s.launch_server, s.stop_web_tunnel).
Cross-router calls go through `s.` at call time, so registration order does not matter for them.
"""
from __future__ import annotations

import platform
import threading
import time
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request

from core import fsutil, netinfo, notifier
from webapp import common
from webapp.auth import LoginLimiter, SessionStore, authenticate
from webapp.state import RuntimeState
from webapp.websocket_manager import WebSocketManager


class Services(SimpleNamespace):
    runtime: RuntimeState
    append_console_line: Any
    notify: Any


def require_auth(request: Request) -> str:
    """Session cookie (browser) or HTTP Basic (scripts); both share the failed-login lockout."""
    state = request.app.state
    return authenticate(request, state.sessions, state.login_limiter, state.on_lockout)


def create_services(app: FastAPI) -> Services:
    runtime = RuntimeState()
    app.state.runtime = runtime
    app.state.console_manager = WebSocketManager()

    def append_console_line(line: str) -> None:
        app.state.console_manager.publish(line)

    fsutil.set_logger(lambda message: append_console_line(f"[config] {message}"))
    common._config_warning_sinks[:] = [append_console_line]

    # ---------- Alerts (email + heartbeat) ----------
    last_alert_at: dict[str, float] = {}

    def notify(category: str, subject: str, body: str, throttle: bool = True) -> None:
        """Email an alert in the background if enabled for `category` (server / link / tunnels).

        The same subject is sent at most once per ALERT_REPEAT_SEC so a flapping service can't spam.
        """
        config = notifier.load_config()
        if not notifier.email_ready(config) or not config.get(f"notify_{category}", True):
            return
        now = time.monotonic()
        if throttle and now - last_alert_at.get(subject, -common.ALERT_REPEAT_SEC) < common.ALERT_REPEAT_SEC:
            return
        last_alert_at[subject] = now
        full_body = (
            f"{body}\n\n"
            f"Host: {platform.node() or 'unknown'}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Dashboard: {runtime.web_tunnel_url or f'http://{netinfo.lan_ip()}:{common.WEB_PORT} (LAN)'}\n"
        )

        def send() -> None:
            try:
                notifier.send_email(config, f"[EZ Minecraft] {subject}", full_body)
                append_console_line(f"[notify] email sent: {subject}")
            except Exception as exc:
                append_console_line(f"[notify] email failed: {exc}")

        threading.Thread(target=send, name="notify", daemon=True).start()

    # ---------- Login sessions and failed-login lockout ----------
    app.state.sessions = SessionStore()
    app.state.login_limiter = LoginLimiter()

    def on_lockout(ip: str) -> None:
        append_console_line(f"[auth] Too many wrong passwords from {ip}; sign-in locked for 15 minutes.")
        notify("security", "Sign-in locked after wrong passwords",
               f"Someone at {ip} entered a wrong dashboard password too many times. Sign-in from there is "
               "locked for 15 minutes.\n\nIf this wasn't you, change EZ_WEBAPP_PASSWORD to a long, unique one.")

    app.state.on_lockout = on_lockout

    def heartbeat_loop() -> None:
        while not runtime.shutting_down:
            url = notifier.load_config().get("heartbeat_url")
            if url:
                notifier.ping_heartbeat(url)
            time.sleep(common.HEARTBEAT_INTERVAL_SEC)

    return Services(app=app, runtime=runtime, append_console_line=append_console_line, notify=notify,
                    heartbeat_loop=heartbeat_loop)
