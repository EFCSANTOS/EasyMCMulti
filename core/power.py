"""Keeping an Android phone awake for the server (Termux), and noticing when it slept anyway.

`termux-wake-lock` asks Termux to hold a partial wake lock. Android's Doze mode ignores the wake
locks of apps that are battery-optimized, and some brands (Xiaomi, Samsung, Huawei, …) freeze
background apps anyway. So with the screen off the phone can still fall into deep sleep: the CPU
stops, Wi-Fi goes quiet and nobody can reach the server until someone turns the screen on. Only the
owner can lift that, in Android's battery settings for Termux; `open_battery_settings()` brings up
that screen.

The sleep check compares two clocks: CLOCK_BOOTTIME keeps counting while the device is suspended and
CLOCK_MONOTONIC does not, so the gap between them grows by exactly the time spent asleep.
"""
import shutil
import subprocess
import time

TERMUX_PACKAGE = "com.termux"


def can_detect_sleep():
    return hasattr(time, "CLOCK_BOOTTIME")  # Linux and Android; not Windows or macOS


def asleep_total():
    """Seconds this device has spent suspended since it booted."""
    # Monotonic first: the later boottime read can only add the microseconds in between, never go negative.
    monotonic = time.clock_gettime(time.CLOCK_MONOTONIC)
    return max(0.0, time.clock_gettime(time.CLOCK_BOOTTIME) - monotonic)


class SleepWatch:
    """check() returns how long the device was asleep since the previous check (0 the first time)."""

    def __init__(self, read=asleep_total):
        self._read = read
        self._last = None

    def check(self):
        now = self._read()
        slept = 0.0 if self._last is None else now - self._last
        self._last = now
        return max(0.0, slept)


def _run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def acquire_wake_lock():
    """Ask Termux for its wake lock again (a no-op while it is held). False when not in Termux."""
    return shutil.which("termux-wake-lock") is not None and _run(["termux-wake-lock"])


def open_battery_settings():
    """Show Android's "let Termux run in the background" prompt on the phone. False if `am` failed.

    The direct prompt needs Termux to be allowed to open screens from the background; the list of
    battery-optimized apps is the fallback.
    """
    if shutil.which("am") is None:
        return False
    return (_run(["am", "start", "-a", "android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
                  "-d", f"package:{TERMUX_PACKAGE}"])
            or _run(["am", "start", "-a", "android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS"]))
