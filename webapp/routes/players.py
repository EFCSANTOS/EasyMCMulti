"""Players page: whitelist, operators and bans (FEAT-02).

Server running → console commands (the server updates its JSON files and resolves UUIDs).
Server stopped → the JSON files are edited directly (core/players.py), under the lifecycle lock so
a start can't race the edit.
"""
from __future__ import annotations

import ipaddress
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import activity, config_store, players
from webapp import common
from webapp.services import Services, require_auth
from webapp.validation import require_player, validate_config_update

KINDS = ("whitelist", "ops", "bans", "ips")
# How long to wait for the running server to rewrite its JSON file after a command.
SYNC_WAIT_SEC = 3.0
MAX_REASON_LENGTH = 100


def _reason(value: Any) -> str:
    reason = str(value or "").strip()
    if any(ord(char) < 32 for char in reason) or len(reason) > MAX_REASON_LENGTH:
        raise HTTPException(status_code=400, detail=f"The reason must be one line of at most {MAX_REASON_LENGTH} characters.")
    return reason


def _ip(value: Any) -> str:
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        raise HTTPException(status_code=400, detail="A valid IP address is required.") from None


def _level(value: Any) -> int:
    try:
        level = int(value if value not in (None, "") else players.DEFAULT_OP_LEVEL)
    except (TypeError, ValueError):
        level = 0
    if not 1 <= level <= 4:
        raise HTTPException(status_code=400, detail="Operator level must be 1, 2, 3 or 4.")
    return level


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    def server_dir() -> str:
        return common._server_dir_for_config(common._effective_config())

    def lists_payload(message: str = "") -> dict[str, Any]:
        config = common._effective_config()
        sdir = common._server_dir_for_config(config)
        whitelist = players.read_list(sdir, "whitelist")
        listed = {str(entry.get("name", "")).lower() for entry in whitelist}
        return {
            "whitelist": [{"name": entry.get("name", "")} for entry in whitelist],
            "ops": [{"name": entry.get("name", ""), "level": entry.get("level", players.DEFAULT_OP_LEVEL)}
                    for entry in players.read_list(sdir, "ops")],
            "bans": [{"name": entry.get("name", ""), "reason": entry.get("reason", "")}
                     for entry in players.read_list(sdir, "bans")],
            "ips": [{"ip": entry.get("ip", ""), "reason": entry.get("reason", "")}
                    for entry in players.read_list(sdir, "ips")],
            "recent": [{**player, "whitelisted": player["name"].lower() in listed} for player in runtime.recent()],
            "whitelist_enabled": bool(config.get("white_list")),
            "online_mode": bool(config.get("online_mode", True)),
            "server_running": s.server_running(),
            "message": message,
        }

    def send(command: str) -> None:
        runtime.process.send(command)
        append_console_line(f"> {command}")

    def wait_for_file(kind: str, name: str, present: bool) -> None:
        """Give the running server a moment to rewrite its JSON file after a command."""
        sdir = server_dir()
        deadline = time.monotonic() + SYNC_WAIT_SEC
        while time.monotonic() < deadline:
            listed = (any(entry.get("ip") == name for entry in players.read_list(sdir, kind)) if kind == "ips"
                      else players.contains(sdir, kind, name))
            if listed == present:
                return
            time.sleep(0.1)

    def set_whitelist(enabled: bool) -> str:
        config = common._effective_config()
        config.update(validate_config_update({"white_list": enabled}, common._host_ram_mb()))
        config_store.save(config)
        if s.server_running():
            send("whitelist on" if enabled else "whitelist off")
        return "Whitelist on: only listed players can join." if enabled else "Whitelist off: anyone can join."

    COMMANDS = {
        ("whitelist", "add"): "whitelist add {name}",
        ("whitelist", "remove"): "whitelist remove {name}",
        ("ops", "add"): "op {name}",
        ("ops", "remove"): "deop {name}",
        ("bans", "add"): "ban {name} {reason}",
        ("bans", "remove"): "pardon {name}",
        ("ips", "remove"): "pardon-ip {name}",
    }
    DONE = {
        ("whitelist", "add"): "{name} can now join.",
        ("whitelist", "remove"): "{name} was removed from the whitelist.",
        ("ops", "add"): "{name} is now an operator.",
        ("ops", "remove"): "{name} is no longer an operator.",
        ("bans", "add"): "{name} is banned.",
        ("bans", "remove"): "{name} was unbanned.",
        ("ips", "remove"): "{name} was unbanned.",
    }

    @app.get("/api/players/activity")
    def player_activity(_: str = Depends(require_auth)) -> dict[str, Any]:
        return activity.summary()

    @app.get("/api/players/lists")
    def player_lists(_: str = Depends(require_auth)) -> dict[str, Any]:
        return lists_payload()

    @app.post("/api/players/{kind}")
    def change_player_list(kind: str, payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        if kind not in KINDS:
            raise HTTPException(status_code=404, detail="Unknown list.")
        action = str(payload.get("action", "")).lower()
        if kind == "whitelist" and action in ("enable", "disable"):
            return lists_payload(set_whitelist(action == "enable"))
        if (kind, action) not in COMMANDS:
            raise HTTPException(status_code=400, detail=f"Unsupported action for {kind}: {action or '(none)'}")
        name = _ip(payload.get("ip") or payload.get("name")) if kind == "ips" else require_player(payload.get("name"))
        reason = _reason(payload.get("reason")) if kind == "bans" and action == "add" else ""
        level = _level(payload.get("level")) if kind == "ops" and action == "add" else players.DEFAULT_OP_LEVEL

        if s.server_running():
            send(COMMANDS[(kind, action)].format(name=name, reason=reason).strip())
            wait_for_file(kind, name, present=(action == "add"))
            message = DONE[(kind, action)].format(name=name)
            if kind == "ops" and action == "add" and level != players.DEFAULT_OP_LEVEL:
                message += " (Levels other than 4 can only be set while the server is stopped.)"
            return lists_payload(message)

        if not runtime.lifecycle.acquire(blocking=False):
            raise s.busy_error()
        try:
            sdir = server_dir()
            if action == "add":
                online_mode = bool(common._effective_config().get("online_mode", True))
                try:
                    name = players.add(sdir, kind, name, online_mode, level=level, reason=reason)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            elif not players.remove(sdir, kind, name):
                raise HTTPException(status_code=404, detail=f"{name} is not on that list.")
        finally:
            runtime.lifecycle.release()
        return lists_payload(DONE[(kind, action)].format(name=name))
