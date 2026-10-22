"""Host platform detection (Windows, Termux on Android, generic Linux)."""
import os
import sys


def is_windows():
    return sys.platform == "win32"


def is_termux():
    """True when running inside Termux on Android."""
    return "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", "")


def termux_prefix():
    """Termux install prefix ($PREFIX), e.g. /data/data/com.termux/files/usr."""
    return os.environ.get("PREFIX", "/data/data/com.termux/files/usr")


def describe():
    if is_windows():
        return "windows"
    if is_termux():
        return "termux"
    return sys.platform
