"""Host platform detection (Windows, Termux on Android, generic Linux) + Termux package installs."""
import collections
import os
import shutil
import subprocess
import sys
import threading

# Same non-interactive options as termux-setup.sh: answer dpkg's config-file prompts automatically.
APT_OPTIONS = ["-y", "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]
_PACKAGE_LOCK = threading.Lock()  # apt/dpkg allow one install at a time


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


def _apt(args, log, timeout_sec):
    """Run apt-get without prompts; return (exit code, last output lines)."""
    apt = shutil.which("apt-get")
    if not apt:
        return 127, ["apt-get not found"]
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    tail = collections.deque(maxlen=8)
    try:
        proc = subprocess.Popen([apt, *APT_OPTIONS, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line:
                tail.append(line)
            if line.startswith(("Get:", "Setting up", "Unpacking")):
                log(line)  # progress without apt's full noise
        return proc.wait(timeout=timeout_sec), list(tail)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, [str(exc)]


def termux_install(package, log=print, timeout_sec=1800):
    """Install a Termux package without prompts. Raises RuntimeError with a manual hint on failure.

    A "package not found" usually means stale package lists, so the lists are refreshed and the
    install is retried once.
    """
    if not is_termux():
        raise RuntimeError(f"Automatic install of {package} is only available in Termux.")
    with _PACKAGE_LOCK:
        log(f"Installing {package} with Termux's package manager (this can take a few minutes)...")
        code, tail = _apt(["install", package], log, timeout_sec)
        if code != 0:
            log("Refreshing the package lists and retrying...")
            _apt(["update"], log, timeout_sec)
            code, tail = _apt(["install", package], log, timeout_sec)
        if code != 0:
            details = " / ".join(tail[-3:])
            raise RuntimeError(f"Could not install {package} automatically ({details}). "
                               f"Install it in Termux with: pkg install {package}")
        log(f"{package} installed.")
