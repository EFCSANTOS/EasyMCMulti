"""Deep sleep on phones: keep Termux's wake lock, notice when the phone slept anyway, help fix it."""
from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import hostenv, power
from webapp import common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line
    watch = power.SleepWatch(read=lambda: power.asleep_total())  # looked up per call so tests can fake it
    last_wake_lock: list[float | None] = [None]  # None: take it on the first check

    def describe(seconds: float) -> str:
        minutes = round(seconds / 60)
        return f"{minutes} minute(s)" if minutes < 120 else f"{seconds / 3600:.1f} hours"

    def power_tick() -> None:
        """One check: record a deep sleep since the last one, keep the wake lock (Termux)."""
        slept = watch.check() if power.can_detect_sleep() else 0.0
        termux = hostenv.is_termux()
        if slept >= common.SLEEP_REPORT_SEC:
            with runtime.lock:
                runtime.sleep_events.append((time.time(), slept))
            what = "phone" if termux else "computer"
            append_console_line(f"[power] the {what} was in deep sleep for {describe(slept)}; "
                                "the server was unreachable meanwhile")
            body = (f"The {what} was in deep sleep for {describe(slept)}, so the server, the tunnel and the "
                    "dashboard were unreachable until it woke up (often only when someone turned the screen on).")
            if termux:
                body += ("\n\nFix it once on the phone: Settings → Apps → Termux → Battery → Unrestricted (or \"Don't "
                         "optimize\"). On Xiaomi, Samsung or Huawei also allow Autostart / turn off \"Put unused apps "
                         "to sleep\" for Termux. Keeping the phone on the charger helps too. In Termux, `ezmc battery` "
                         "opens the setting.")
            # Wi-Fi takes a moment to come back after waking; send once it has.
            timer = threading.Timer(common.SLEEP_ALERT_DELAY_SEC, s.notify,
                                    args=("server", f"The {what} fell asleep: server unreachable", body))
            timer.daemon = True
            timer.start()
        now = time.monotonic()
        due = last_wake_lock[0] is None or now - last_wake_lock[0] >= common.WAKE_LOCK_REFRESH_SEC
        if termux and (slept >= common.SLEEP_REPORT_SEC or due):
            # Taken by run.sh at start; take it again in case it was released (notification button, Termux restart).
            last_wake_lock[0] = now
            if not power.acquire_wake_lock():
                append_console_line("[power] could not take Termux's wake lock (is termux-tools installed?)")

    def power_loop() -> None:
        if not power.can_detect_sleep() and not hostenv.is_termux():
            return  # Windows/macOS: nothing to watch
        while not runtime.shutting_down:
            try:
                power_tick()
            except Exception as exc:  # keep watching whatever happens
                append_console_line(f"[power] error: {exc}")
            time.sleep(common.POWER_CHECK_SEC)

    def sleep_summary() -> dict[str, Any]:
        cutoff = time.time() - 24 * 3600
        with runtime.lock:
            recent = [(at, seconds) for at, seconds in runtime.sleep_events if at >= cutoff]
        last = recent[-1] if recent else None
        return {"count": len(recent), "total_seconds": round(sum(seconds for _, seconds in recent)),
                "last_at": last[0] if last else None, "last_seconds": round(last[1]) if last else 0,
                "phone": hostenv.is_termux()}

    def reset_power() -> None:
        watch.__init__(read=lambda: power.asleep_total())
        last_wake_lock[0] = None

    s.power_tick = power_tick
    s.reset_power = reset_power
    s.power_loop = power_loop
    s.sleep_summary = sleep_summary

    @app.get("/api/power")
    def power_info(_: str = Depends(require_auth)) -> dict[str, Any]:
        return sleep_summary()

    @app.post("/api/power/battery-settings")
    def battery_settings(_: str = Depends(require_auth)) -> dict[str, Any]:
        if not hostenv.is_termux():
            raise HTTPException(status_code=400, detail="This only applies when the server runs on an Android phone.")
        if not power.open_battery_settings():
            raise HTTPException(status_code=502, detail="Android did not open the setting. In Termux, run: ezmc battery")
        return {"ok": True, "message": "Look at the phone: allow Termux to run in the background (Unrestricted)."}

    @app.post("/api/power/dismiss")
    def dismiss(_: str = Depends(require_auth)) -> dict[str, Any]:
        with runtime.lock:
            runtime.sleep_events.clear()
        return sleep_summary()
