"""Device temperatures in °C, from whatever this platform lets an app read.

Order: psutil sensors (Linux PCs), the kernel's thermal zones and battery sensor under /sys (Linux and
many Android phones), then `termux-battery-status` (Termux:API) when Android hides /sys. Windows
only exposes temperatures to administrators, so there the list is empty.

Each reading is {"label", "celsius", "kind"}: kind "battery" is kept apart because a battery is
too hot at a much lower temperature than a CPU.
"""
import glob
import json
import os
import shutil
import subprocess
import time

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - not installed on Termux
    psutil = None

SYS_DIR = "/sys"
# Anything outside this range is a sensor that isn't wired up (0, -273, 255…), not a temperature.
PLAUSIBLE = (1.0, 150.0)
TERMUX_CACHE_SEC = 30
_termux_cache = {"at": -TERMUX_CACHE_SEC, "readings": []}


def _reading(label, celsius, kind="device"):
    celsius = round(float(celsius), 1)
    if not PLAUSIBLE[0] <= celsius <= PLAUSIBLE[1]:
        return None
    return {"label": label, "celsius": celsius, "kind": kind}


def _from_psutil():
    sensors = getattr(psutil, "sensors_temperatures", None) if psutil is not None else None
    if sensors is None:
        return []
    try:
        found = sensors() or {}
    except Exception:
        return []
    readings = []
    for chip, entries in found.items():
        for index, entry in enumerate(entries):
            label = entry.label or (chip if len(entries) == 1 else f"{chip} {index + 1}")
            kind = "battery" if "batt" in chip.lower() else "device"
            readings.append(_reading(label, entry.current, kind))
    return [r for r in readings if r]


def _read_number(path):
    with open(path, "r", encoding="utf-8") as handle:
        return float(handle.read().strip())


def _from_sys():
    readings = []
    for zone in sorted(glob.glob(os.path.join(SYS_DIR, "class", "thermal", "thermal_zone*"))):
        try:
            value = _read_number(os.path.join(zone, "temp"))
            with open(os.path.join(zone, "type"), "r", encoding="utf-8") as handle:
                label = handle.read().strip() or os.path.basename(zone)
        except (OSError, ValueError):
            continue  # Android often refuses some zones
        kind = "battery" if "batt" in label.lower() else "device"
        readings.append(_reading(label, value / 1000 if abs(value) > 1000 else value, kind))
    for supply in sorted(glob.glob(os.path.join(SYS_DIR, "class", "power_supply", "*"))):
        try:
            value = _read_number(os.path.join(supply, "temp"))  # tenths of a degree
        except (OSError, ValueError):
            continue
        name = os.path.basename(supply)
        readings.append(_reading(name.capitalize(), value / 10, "battery" if "batt" in name.lower() else "device"))
    return [r for r in readings if r]


def _from_termux():
    if shutil.which("termux-battery-status") is None:
        return []
    now = time.monotonic()
    if now - _termux_cache["at"] < TERMUX_CACHE_SEC:
        return _termux_cache["readings"]  # the command takes about a second; metrics poll every 2
    readings = []
    try:
        result = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=10)
        reading = _reading("Battery", json.loads(result.stdout)["temperature"], "battery")
        readings = [reading] if reading else []
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    _termux_cache.update(at=now, readings=readings)
    return readings


def read():
    """Every temperature we can see, hottest first."""
    readings = _from_psutil() or _from_sys() or _from_termux()
    return sorted(readings, key=lambda r: r["celsius"], reverse=True)


def hottest(readings, kind):
    """The hottest reading of `kind` ("device" or "battery"), or None."""
    return next((r for r in readings if r["kind"] == kind), None)
