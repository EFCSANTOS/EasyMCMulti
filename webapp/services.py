"""Shared services for the routers: console, alerts, login state and the auth dependency.

`Services` is a plain namespace. create_app() fills the basics here, then each router's
register(app, s) adds the helpers other routers need (e.g. s.launch_server, s.stop_web_tunnel).
Cross-router calls go through `s.` at call time, so registration order does not matter for them.
"""
from __future__ import annotations

import platform
import re
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

    # One-shot watchers for wait_for_console: (compiled pattern, event set when a line matches).
    console_watchers: list[tuple[re.Pattern[str], threading.Event]] = []
    watchers_lock = threading.Lock()

    def append_console_line(line: str) -> None:
        app.state.console_manager.publish(line)
        if console_watchers:
            with watchers_lock:
                for pattern, matched in console_watchers:
                    if pattern.search(line):
                        matched.set()

    def wait_for_console(pattern: str, action: Any, timeout: float) -> bool:
        """Run action(), then wait until a console line matches `pattern`. True if one did in time."""
        entry = (re.compile(pattern), threading.Event())
        with watchers_lock:
            console_watchers.append(entry)
        try:
            action()
            return entry[1].wait(timeout)
        finally:
            with watchers_lock:
                console_watchers.remove(entry)

    fsutil.set_logger(lambda message: append_console_line(f"[config] {message}"))
    common._config_warning_sinks[:] = [append_console_line]

    # ---------- Alerts (email + heartbeat) ----------
    last_alert_at: dict[str, float] = {}

    def notify(category: str, subject: str, body: str, throttle: bool = True) -> None:
        """Send an owner alert by email and/or the owner Discord webhook, in the background, if
        enabled for `category` (server / link / tunnels / updates / security).

        The same subject is sent at most once per ALERT_REPEAT_SEC so a flapping service can't spam.
        """
        config = notifier.load_config()
        email = notifier.email_ready(config)
        discord_url = config.get("discord_owner_url")
        if not (email or discord_url) or not config.get(f"notify_{category}", True):
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
            if email:
                try:
                    notifier.send_email(config, f"[EZ Minecraft] {subject}", full_body)
                    append_console_line(f"[notify] email sent: {subject}")
                except Exception as exc:
                    append_console_line(f"[notify] email failed: {exc}")
            if discord_url:
                try:
                    notifier.send_discord(discord_url, f"**{subject}**\n{full_body}")
                    append_console_line(f"[notify] Discord (owner) sent: {subject}")
                except Exception as exc:
                    append_console_line(f"[notify] Discord (owner) failed: {exc}")

        threading.Thread(target=send, name="notify", daemon=True).start()

    # ---------- Friends channel (Discord) ----------
    # Joins/leaves within FRIENDS_BATCH_SEC go out as one message ("Alex, Sam joined · Bob left").
    pending_players: dict[str, list[str]] = {"joined": [], "left": []}
    players_lock = threading.Lock()

    def notify_friends(kind: str, text: str) -> None:
        """Post to the friends webhook if set and `kind` (status / players / link) is switched on."""
        config = notifier.load_config()
        url = config.get("discord_friends_url")
        if not url or not config.get(f"friends_notify_{kind}", True):
            return

        def send() -> None:
            try:
                notifier.send_discord(url, text)
            except Exception as exc:
                append_console_line(f"[notify] Discord (friends) failed: {exc}")

        threading.Thread(target=send, name="notify-friends", daemon=True).start()

    def flush_player_events() -> None:
        with players_lock:
            joined, left = pending_players["joined"], pending_players["left"]
            pending_players["joined"], pending_players["left"] = [], []
        parts = []
        if joined:
            parts.append(f"{', '.join(joined)} joined")
        if left:
            parts.append(f"{', '.join(left)} left")
        if parts:
            notify_friends("players", " · ".join(parts))

    def player_event(name: str, event: str) -> None:
        with players_lock:
            first = not (pending_players["joined"] or pending_players["left"])
            other = "left" if event == "joined" else "joined"
            if name in pending_players[other]:
                pending_players[other].remove(name)  # joined and left again within the batch: say nothing
            else:
                pending_players[event].append(name)
        if first:
            timer = threading.Timer(common.FRIENDS_BATCH_SEC, flush_player_events)
            timer.daemon = True
            timer.start()

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

    def reset_notifications() -> None:
        """Forget alert throttles and pending friend join/leave batches (tests start clean)."""
        last_alert_at.clear()
        with players_lock:
            pending_players["joined"].clear()
            pending_players["left"].clear()

    return Services(app=app, runtime=runtime, append_console_line=append_console_line, notify=notify,
                    reset_notifications=reset_notifications,
                    wait_for_console=wait_for_console, notify_friends=notify_friends, player_event=player_event,
                    flush_player_events=flush_player_events, heartbeat_loop=heartbeat_loop)
