"""Device health alerts: high CPU / RAM use and high temperature (checked by the scheduler)."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from core import notifier, temperatures
from webapp import common
from webapp.services import Services

# Back to normal only this far below the limit, so a value hovering at the limit can't flap.
RECOVER_MARGIN = 5
# A temperature must stay high for this many checks (one a minute): one hot reading is often a blip.
TEMP_CHECKS = 2


def register(app: FastAPI, s: Services) -> None:
    append_console_line = s.append_console_line
    cpu_last: dict[str, tuple[float, float]] = {}  # our own CPU sample: the average since the last check
    # watch -> {"over": consecutive checks above the limit, "alerted": alert sent and not recovered yet}
    state: dict[str, dict[str, Any]] = {}

    def check(watch: str, value: float | None, limit: float, needed: int, category: str,
              subject: str, body: str, recovered: str) -> None:
        entry = state.setdefault(watch, {"over": 0, "alerted": False})
        if value is None:
            entry["over"] = 0
            return
        if value >= limit:
            entry["over"] += 1
            if entry["over"] >= needed and not entry["alerted"]:
                entry["alerted"] = True
                append_console_line(f"[health] {subject}")
                s.notify(category, subject, body, throttle=False)
            return
        entry["over"] = 0
        if entry["alerted"] and value <= limit - RECOVER_MARGIN:
            entry["alerted"] = False
            append_console_line(f"[health] {recovered}")
            s.notify(category, recovered, f"{recovered}.", throttle=False)

    def health_tick() -> None:
        config = notifier.load_config()
        cpu, scope = common._cpu_usage(cpu_last)
        used, total = common._memory_usage()
        ram = used / total * 100 if total > 1 else None
        minutes = int(config["alert_usage_minutes"])
        where = " (Termux processes)" if scope == "processes" else ""
        check("cpu", cpu if scope != "unavailable" else None, config["alert_cpu_percent"], minutes, "usage",
              f"CPU use is high: {cpu:.0f}%{where}",
              f"CPU use has been at {config['alert_cpu_percent']}% or more for {minutes} minute(s); it is "
              f"{cpu:.0f}% now. Players may notice lag. Check the console for a busy mod or too many "
              "loaded chunks, or lower the view distance.", "CPU use is back to normal")
        check("ram", ram, config["alert_ram_percent"], minutes, "usage",
              f"Memory use is high: {ram or 0:.0f}%",
              f"Memory use has been at {config['alert_ram_percent']}% or more for {minutes} minute(s); it is "
              f"{ram or 0:.0f}% now. The device may start closing apps, the server included. Close other "
              "apps or give the server less RAM in Server setup.", "Memory use is back to normal")

        readings = temperatures.read()
        for kind, limit_key, name in (("device", "alert_temp_c", "Device"), ("battery", "alert_battery_c", "Battery")):
            hottest = temperatures.hottest(readings, kind)
            celsius = hottest["celsius"] if hottest else None
            label = f" ({hottest['label']})" if hottest else ""
            check(f"temp-{kind}", celsius, config[limit_key], TEMP_CHECKS, "temperature",
                  f"{name} is too hot: {celsius or 0:.0f} °C",
                  f"{name} temperature{label} is {celsius or 0:.0f} °C, at or above your {config[limit_key]} °C "
                  "limit. Heat slows the device down and wears the battery. Move it somewhere cooler, take it "
                  "out of its case or off the charger, or stop the server for a while.",
                  f"{name} temperature is back to normal")

    def reset_health() -> None:
        cpu_last.clear()
        state.clear()

    s.health_tick = health_tick
    s.reset_health = reset_health
