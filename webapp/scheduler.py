"""One background tick for everything time-based: backups (FEAT-01), the daily restart and the
idle stop (FEAT-06). `scheduler_loop` is started by autostart; tests call `scheduler_tick(now)`.

Quiet hours (no autostart / crash restart at night) are checked where those starts happen
(`launch_when_free`), via `common._in_quiet_hours`.
"""
from __future__ import annotations

import datetime
import math
import time
from typing import Any

from fastapi import FastAPI, HTTPException

from core import mod_updates, paths
from webapp import common
from webapp.services import Services

# Chat warnings before the daily restart (minutes), only when someone is online.
RESTART_WARNINGS = (10, 5, 1)
# Don't do a restart that was missed by more than this (e.g. the dashboard was off at that time).
RESTART_LATE_LIMIT = datetime.timedelta(minutes=30)


def _at(now: datetime.datetime, hhmm: str) -> datetime.datetime | None:
    try:
        hours, minutes = (int(part) for part in hhmm.split(":"))
    except (AttributeError, ValueError):
        return None
    return now.replace(hour=hours, minute=minutes, second=0, microsecond=0)


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    def say(text: str) -> None:
        process = runtime.process
        if process is not None and process.is_running():
            process.send(f"say {text}")
            append_console_line(f"> say {text}")

    def daily_restart_tick(now: datetime.datetime, config: dict[str, Any]) -> None:
        target = _at(now, str(config.get("restart_daily_time") or ""))
        if target is None or not s.server_running() or runtime.daily_restart_done == now.date().isoformat():
            return
        minutes_left = (target - now).total_seconds() / 60
        if 0 < minutes_left <= max(RESTART_WARNINGS):
            due = {m for m in RESTART_WARNINGS if m >= minutes_left} - runtime.restart_warnings_sent
            if due and runtime.online_players:
                runtime.restart_warnings_sent.update(due)
                left = math.ceil(minutes_left)  # e.g. someone joined 3 minutes before: say 3, not 5
                say(f"Server restarting in {left} minute{'s' if left != 1 else ''}.")
            return
        if not datetime.timedelta(0) <= now - target <= RESTART_LATE_LIMIT:
            return
        runtime.daily_restart_done = now.date().isoformat()
        runtime.restart_warnings_sent.clear()
        append_console_line("[schedule] daily restart")
        say("Server restarting now. Back in a minute!")
        s.safety_backup("restart")
        try:
            s.start_lifecycle_job("restarting", s.restart_job, "Scheduled restart failed")
            s.notify_friends("status", "Daily restart: the server is back in a minute.")
        except HTTPException:
            runtime.daily_restart_done = ""  # busy right now: try again on the next tick
            append_console_line("[schedule] daily restart postponed: another server operation is running")

    def idle_tick(now: datetime.datetime, config: dict[str, Any]) -> None:
        minutes = int(config.get("idle_stop_minutes") or 0)
        if minutes <= 0 or not s.server_running() or runtime.online_players or runtime.empty_since is None:
            return
        if now.timestamp() - runtime.empty_since < minutes * 60:
            return
        if not runtime.lifecycle.acquire(blocking=False):
            return  # something else is starting/stopping it; check again next minute
        try:
            runtime.stopped_reason = "idle"
            s.request_stop()
        finally:
            runtime.lifecycle.release()
        append_console_line(f"[schedule] nobody online for {minutes} min; stopping the server")
        s.notify_friends("status", f"Server stopped: nobody was online for {minutes} minutes. "
                                   "Ask the owner, or start it from the dashboard.")

    def old_mods_tick(now: datetime.datetime, config: dict[str, Any]) -> None:
        """Delete the jars kept by a mod update once the updated server has run fine for a while."""
        if not s.server_running() or now.timestamp() - runtime.last_start_at < common.OLD_MODS_KEEP_SEC:
            return
        folder = paths.content_folder(str(config.get("loader", "vanilla")), common._server_dir_for_config(config))
        if folder and mod_updates.old_jars(folder):
            removed = mod_updates.forget_old(folder)
            append_console_line(f"[mods] the updated mods work; removed {removed} old jar(s)")

    def scheduler_tick(now: datetime.datetime | None = None) -> None:
        now = now or datetime.datetime.now()
        config = common._effective_config()
        s.backup_tick(now.timestamp())
        daily_restart_tick(now, config)
        idle_tick(now, config)
        old_mods_tick(now, config)

    def scheduler_loop() -> None:
        while not runtime.shutting_down:
            time.sleep(common.SCHEDULER_TICK_SEC)
            try:
                scheduler_tick()
            except Exception as exc:  # keep ticking whatever happens
                append_console_line(f"[schedule] error: {exc}")

    s.scheduler_tick = scheduler_tick
    s.scheduler_loop = scheduler_loop
