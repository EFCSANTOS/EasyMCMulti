"""playit.gg integration: tunnel for remote play without opening a port on the router.

IMPORTANT (agent v1.0.10+): the playit binary is a pure *daemon*. It must be given a
SECRET KEY (--secret); started without one, it waits for IPC provisioning and exits.

Instead of asking the user to fiddle with the playit website by hand, everything is
AUTOMATED through the official API (api.playit.gg):
  1. generate a "claim code" (5 hex bytes); the dashboard shows https://playit.gg/claim/<code>
  2. the user opens it and clicks "Allow" in any browser (the only manual step, equivalent to a login)
  3. exchange the code for a permanent SECRET KEY (/claim/exchange)
  4. create a "Minecraft Java" tunnel -> localhost:25565 automatically (/tunnels/create)
  5. fetch the allocated public address (/tunnels/list)
  6. run the daemon: playit --secret <KEY>
The web dashboard is NOT tunnelled through playit: the API no longer creates generic TCP tunnels
("TunnelTypeRequiresDescription", 2026-10-22). It uses a Cloudflare quick tunnel instead (cloudflared.py).

Secret + address are stored LOCALLY (EZ_Server_Data/playit_config.json), never committed.

API refs (official client): https://github.com/playit-cloud/playit-api-java
Auth: header  Authorization: Agent-Key <secret>
"""
import json
import os
import platform
import secrets
import shutil
import subprocess
import threading
import time

import requests

from . import hostenv, paths

RELEASES_API = "https://api.github.com/repos/playit-cloud/playit-agent/releases/latest"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

API_BASE = "https://api.playit.gg"
CLAIM_PAGE = "https://playit.gg/claim/{}"
MINECRAFT_TUNNEL_NAME = "EZ Minecraft"

_CONFIG_FILE = os.path.join(paths.DATA_DIR, "playit_config.json")


def _agent_binary_name() -> str:
    """Return the PlayIt agent binary that matches the current platform.

    The upstream PlayIt project ships native Linux binaries and Android packages,
    but not a universal cross-platform daemon. We therefore select the right
    binary for the current runtime instead of hardcoding a Windows .exe.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system.startswith("win"):
        if machine in {"amd64", "x86_64", "x64"}:
            return "playit-windows-x86_64.exe"
        if machine in {"x86", "i386", "i686"}:
            return "playit-windows-x86.exe"
        raise RuntimeError(f"Unsupported Windows architecture for PlayIt: {machine!r}")

    if system in {"linux", "android"}:
        if machine in {"amd64", "x86_64", "x64"}:
            return "playit-linux-amd64"
        if machine in {"arm64", "aarch64"}:
            return "playit-linux-aarch64"
        if machine in {"armv7", "armv7l", "armhf"}:
            return "playit-linux-armv7"
        if machine in {"i386", "i686"}:
            return "playit-linux-i686"
        raise RuntimeError(f"Unsupported Linux/Android architecture for PlayIt: {machine!r}")

    raise RuntimeError(f"PlayIt is not supported on platform={system!r} arch={machine!r}.")


def _agent_path() -> str:
    return os.path.join(paths.DATA_DIR, _agent_binary_name())


# ---------- Local config (secret key + tunnel address) ----------
def load_config():
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(secret, address=""):
    paths.ensure_dirs()
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"secret": secret, "address": address}, f, indent=2)


def forget_config():
    """Delete the saved secret and addresses (used to link a different playit.gg account)."""
    try:
        os.remove(_CONFIG_FILE)
    except FileNotFoundError:
        pass


def saved_address():
    return load_config().get("address", "")


# ---------- playit API calls ----------
def _api(path, body, secret=None):
    """POST JSON to the playit API. Raises RuntimeError on application-level failure.

    NOTE: the API often returns HTTP 400 WITH a useful JSON body
    (e.g. {"status":"fail","data":"AgentVersionTooOld"}). The body must therefore be read
    BEFORE looking at the HTTP status, otherwise the real error message is lost.
    """
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Agent-Key {secret}"
    r = requests.post(API_BASE + path, json=body, headers=headers, timeout=30)
    try:
        data = r.json()
    except ValueError:
        r.raise_for_status()
        raise RuntimeError(f"Non-JSON playit response (HTTP {r.status_code}) from {path}")
    if isinstance(data, dict) and data.get("status") in ("fail", "error"):
        raise RuntimeError(f"playit API {path}: {data.get('data')}")
    if not r.ok:
        raise RuntimeError(f"playit API {path} HTTP {r.status_code}: {data}")
    return data


def claim_generate():
    return secrets.token_hex(5)  # 5 bytes -> 10 hex characters (format expected by the API)


def claim_url(code):
    return CLAIM_PAGE.format(code)


def _claim_setup(code):
    res = _api("/claim/setup", {"code": code, "agent_type": "self-managed",
                                "version": "playit-ez-launcher 1.0"})
    return res.get("data")  # WaitingForUserVisit | WaitingForUser | UserAccepted | UserRejected


def _claim_exchange(code):
    """Return the secret key if available, otherwise None (claim not approved yet).

    Until the user approves, the API answers 400 (or status 'fail'):
    treat that as 'not ready yet' and let the polling loop retry.
    """
    try:
        res = _api("/claim/exchange", {"code": code})
    except (requests.HTTPError, RuntimeError):
        return None
    return res.get("data", {}).get("secret_key")


def _agent_id(secret):
    res = _api("/agents/rundata", {}, secret=secret)
    return res.get("data", {}).get("agent_id")


def _find_tunnel(secret, matches):
    res = _api("/tunnels/list", {}, secret=secret)
    for t in res.get("data", {}).get("tunnels", []):
        if matches(t):
            return t
    return None


def _is_mc_tunnel(t):
    return t.get("tunnel_type") == "minecraft-java"


def _tunnel_address(t):
    """Public address to give to players, or None if not allocated yet."""
    alloc = t.get("alloc", {})
    if alloc.get("status") != "allocated":
        return None
    d = alloc.get("data", {})
    domain = d.get("assigned_domain")
    if not domain:
        return None
    # minecraft-java normally has an SRV record -> the bare domain is enough.
    if d.get("assigned_srv"):
        return domain
    port = d.get("port_start")
    return f"{domain}:{int(port)}" if port else domain


def claim_interactive(log, should_cancel, timeout_sec=300, on_url=None):
    """Generate a claim, publish its approval URL, wait for 'Allow', return the secret.

    on_url(url) receives the approval page so the UI can show it; the user may open it from
    any device. Saves the secret as soon as it is obtained (the tunnel address comes later).
    """
    code = claim_generate()
    url = claim_url(code)
    if on_url:
        on_url(url)
    log(f"Open this page and click 'Allow' to authorize the launcher: {url}")

    deadline = time.time() + timeout_sec
    while True:
        if should_cancel():
            raise RuntimeError("Setup cancelled.")
        if time.time() > deadline:
            raise RuntimeError("Timed out: authorization was not approved in time.")
        state = _claim_setup(code)
        if state == "UserAccepted":
            break
        if state == "UserRejected":
            raise RuntimeError("Authorization was rejected in the browser.")
        time.sleep(1.5)

    log("Authorized. Fetching the key...")
    secret = None
    while secret is None:
        if time.time() > deadline:
            raise RuntimeError("Timed out while exchanging the key.")
        secret = _claim_exchange(code)
        if secret is None:
            time.sleep(1.5)

    save_config(secret, saved_address())
    log("playit key obtained.")
    return secret


def _ensure_tunnel(secret, local_port, log, timeout_sec, matches, name, tunnel_type, label):
    """Ensure a tunnel matching `matches` -> localhost:local_port exists; return its public address.

    IMPORTANT: the API refuses to create a tunnel until the agent has run at least
    once (error 'AgentVersionTooOld'). The daemon must therefore be started BEFORE
    calling this function; creation is retried while the agent registers.
    """
    deadline = time.time() + timeout_sec
    agent_id = _agent_id(secret)

    # Reuse an existing tunnel if it already points to the right local port.
    t = _find_tunnel(secret, matches)
    if t is not None:
        existing = t.get("origin", {}).get("data", {}).get("local_port")
        if existing != local_port:
            log(f"Existing {label} tunnel points to :{existing}, recreating it for :{local_port}...")
            _api("/tunnels/delete", {"tunnel_id": t["id"]}, secret=secret)
            t = None

    if t is None:
        log(f"Creating {label} tunnel -> localhost:{local_port}...")
        body = {
            "name": name,
            "tunnel_type": tunnel_type,  # required: untyped tunnels are rejected by the API
            "port_type": "tcp",
            "port_count": 1,
            "origin": {"type": "agent", "data": {
                "agent_id": agent_id, "local_ip": "127.0.0.1",
                "local_port": local_port}},
            "enabled": True,
        }
        while True:
            try:
                _api("/tunnels/create", body, secret=secret)
                break
            except RuntimeError as e:
                if "AgentVersionTooOld" in str(e) and time.time() < deadline:
                    log("Waiting for the playit agent to register...")
                    time.sleep(3)
                    continue
                raise

    log(f"Waiting for playit to allocate the {label} address...")
    while True:
        if time.time() > deadline:
            raise RuntimeError(f"The {label} tunnel did not receive an address in time.")
        t = _find_tunnel(secret, matches)
        if t:
            addr = _tunnel_address(t)
            if addr:
                return addr
        time.sleep(2)


def ensure_tunnel(secret, local_port, log, timeout_sec=120):
    """Ensure the Minecraft Java tunnel -> localhost:local_port; return the address for players."""
    return _ensure_tunnel(secret, local_port, log, timeout_sec, _is_mc_tunnel,
                          MINECRAFT_TUNNEL_NAME, "minecraft-java", "Minecraft Java")


# ---------- Agent (daemon) download ----------
def ensure_agent(log=print):
    """Download the PlayIt agent matching the current host platform.

    Linux and Android-compatible userlands are supported via the upstream Linux
    binaries; Android will only work when the app is running in a Linux-capable
    environment (for example Termux/proot), not as a normal APK-only app.
    """
    agent_path = _agent_path()
    if os.path.exists(agent_path):
        if os.name != "nt":
            os.chmod(agent_path, 0o755)
        return agent_path

    paths.ensure_dirs()
    log("Looking up the playit.gg agent...")
    rel = requests.get(RELEASES_API, timeout=30).json()

    wanted_name = _agent_binary_name().lower()
    asset = None
    for a in rel.get("assets", []):
        name = a["name"].lower()
        if name == wanted_name:
            asset = a
            break

    if not asset:
        raise RuntimeError(f"No PlayIt binary available for this platform: {wanted_name}")

    log(f"Downloading {asset['name']}...")
    from .downloaders import download
    download(asset["browser_download_url"], agent_path, log=log)
    if os.name != "nt":
        os.chmod(agent_path, 0o755)
    return agent_path


# ---------- Daemon launch ----------
def agent_command(secret):
    """Command line for the agent.

    On Termux the Linux binary cannot find /etc/resolv.conf (Android has none), so DNS fails.
    `termux-chroot` (package `proot`) mounts Termux's etc/ over /etc and fixes that.
    Set EZ_PLAYIT_CHROOT=0 to run the binary directly.
    """
    command = [_agent_path(), "--secret", secret]
    wrapper = shutil.which("termux-chroot")
    if hostenv.is_termux() and wrapper and os.environ.get("EZ_PLAYIT_CHROOT", "1") != "0":
        return [wrapper] + command
    return command


class PlayitAgent:
    """Runs the playit daemon with the secret key and streams its output."""

    def __init__(self, on_output, on_exit):
        self.on_output = on_output
        self.on_exit = on_exit
        self.proc = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, secret):
        if not secret:
            raise RuntimeError("No playit secret key configured.")
        self.proc = subprocess.Popen(
            agent_command(secret),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            self.on_output(line.rstrip("\n"))
        self.on_exit(self.proc.wait())

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
