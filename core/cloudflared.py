"""Cloudflare quick tunnel: an https:// link to the web dashboard, no account needed.

`cloudflared tunnel --url http://127.0.0.1:<port>` prints a random https://<words>.trycloudflare.com
address; traffic is encrypted between the browser and Cloudflare. The address changes every time
cloudflared restarts. Quick tunnels are meant for testing/personal use and have no uptime guarantee.

Binary: Termux -> `pkg install cloudflared` (patched for Android); Windows/Linux -> downloaded from
the official GitHub releases into EZ_Server_Data/ if not already on PATH.
"""
import os
import platform
import re
import shutil
import subprocess
import threading

from . import hostenv, paths

RELEASE_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/{}"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _asset_name():
    """Release asset for this host, e.g. cloudflared-windows-amd64.exe."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {
        "amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
        "arm64": "arm64", "aarch64": "arm64",
        "armv7l": "arm", "armv7": "arm", "armhf": "arm",
        "x86": "386", "i386": "386", "i686": "386",
    }.get(machine)
    if arch is None:
        raise RuntimeError(f"cloudflared is not available for this CPU: {machine!r}")
    if system.startswith("win"):
        return f"cloudflared-windows-{arch}.exe"
    if system == "linux":
        return f"cloudflared-linux-{arch}"
    raise RuntimeError(f"cloudflared is not available for platform {system!r}")


def ensure_binary(log=print):
    """Return a cloudflared executable, downloading it if needed (never on Termux)."""
    on_path = shutil.which("cloudflared")
    if on_path:
        return on_path
    if hostenv.is_termux():
        raise RuntimeError("cloudflared is not installed. Install it in Termux with: pkg install cloudflared")
    path = os.path.join(paths.DATA_DIR, _asset_name())
    if not os.path.exists(path):
        paths.ensure_dirs()
        log("Downloading cloudflared (one time, about 50 MB)...")
        from .downloaders import download
        download(RELEASE_URL.format(_asset_name()), path, log=log)
    if os.name != "nt":
        os.chmod(path, 0o755)
    return path


def find_url(line):
    """The trycloudflare.com address in a cloudflared log line, or None."""
    match = URL_PATTERN.search(line)
    return match.group(0) if match else None


class QuickTunnel:
    """Runs `cloudflared tunnel --url` and reports the public address once it is assigned."""

    def __init__(self, on_url, on_output, on_exit):
        self.on_url = on_url
        self.on_output = on_output
        self.on_exit = on_exit
        self.proc = None
        self.url = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, binary, local_port):
        self.proc = subprocess.Popen(
            [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{local_port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        # The address is printed a few seconds before the tunnel can serve it. Announcing it early
        # makes browsers look it up too soon and cache "name not found" for minutes, so on_url is
        # only called once the first connection to Cloudflare is registered.
        pending_url = None
        registered = False
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if pending_url is None:
                pending_url = find_url(line)
            if "Registered tunnel connection" in line:
                registered = True
            if self.url is None and pending_url and registered:
                self.url = pending_url
                self.on_url(pending_url)
            self.on_output(line)
        self.on_exit(self.proc.wait())

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
