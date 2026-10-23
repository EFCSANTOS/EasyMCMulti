"""Shared test setup: app, client, helpers and fixtures (QUAL-02).

Test files do `from support import *` (helpers and constants); conftest.py re-exports the fixtures
so pytest finds them. Everything here was split out of the former tests/test_webapp_smoke.py.
"""
import importlib

import json

import os

import re

import threading

import time

import zipfile

os.environ["EZ_WEBAPP_USERNAME"] = "admin"

os.environ["EZ_WEBAPP_PASSWORD"] = "s3cret-pass"

import pytest

from fastapi.testclient import TestClient

from core import cloudflared, config_store, downloaders, fsutil, java_manager, modrinth, notifier, paths, playit

from webapp import auth

from webapp.app import create_app

webapp_module = importlib.import_module("webapp.app")

common_module = importlib.import_module("webapp.common")

layout_module = importlib.import_module("webapp.pages.layout")

app = create_app()

client = TestClient(app)

def _dashboard_js():
    """All dashboard scripts, in load order."""
    js_dir = os.path.join(os.path.dirname(webapp_module.__file__), "static", "js")
    return "\n".join(open(os.path.join(js_dir, name), encoding="utf-8").read() for name in layout_module.SCRIPTS)

@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep tests away from the real EZ_Server_Data (launcher config, PlayIt secret, servers)."""
    data_dir = tmp_path / "EZ_Server_Data"
    monkeypatch.setattr(paths, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(paths, "JAVA_DIR", str(data_dir / "java"))
    monkeypatch.setattr(paths, "SERVERS_DIR", str(data_dir / "servers"))
    monkeypatch.setattr(config_store, "_FILE", str(data_dir / "launcher_config.json"))
    monkeypatch.setattr(playit, "_CONFIG_FILE", str(data_dir / "playit_config.json"))
    monkeypatch.setattr(notifier, "_CONFIG_FILE", str(data_dir / "notify_config.json"))
    app.state.sessions.clear()
    app.state.login_limiter.clear()
    client.cookies.clear()

class DummyAgent:
    def __init__(self, on_output=None, on_exit=None):
        self.running = False

    def is_running(self):
        return self.running

    def start(self, secret):
        self.secret = secret
        self.running = True

    def stop(self):
        self.running = False

@pytest.fixture
def fake_playit(monkeypatch):
    """Stub the PlayIt network/agent calls."""
    monkeypatch.setattr(playit, "ensure_agent", lambda log=print: "agent")
    monkeypatch.setattr(playit, "PlayitAgent", DummyAgent)
    monkeypatch.setattr(playit, "ensure_tunnel", lambda secret, port, log, timeout_sec=120, should_abort=None: "demo.example:25565")
    monkeypatch.setattr(playit, "kill_stale_agents", lambda log=print: 0)
    app.state.runtime.playit_agent = None
    app.state.runtime.playit_setup = "idle"
    app.state.runtime.playit_crash_times = []
    yield
    if app.state.runtime.playit_restart_timer is not None:
        app.state.runtime.playit_restart_timer.cancel()
    app.state.runtime.playit_restart_timer = None
    app.state.runtime.playit_agent = None
    app.state.runtime.playit_setup = "idle"
    app.state.runtime.playit_error = ""
    app.state.runtime.playit_crash_times = []

class FakeQuickTunnel:
    """Stand-in for cloudflared.QuickTunnel; the test decides when the URL arrives or it exits."""

    instances: list["FakeQuickTunnel"] = []

    def __init__(self, on_url, on_output, on_exit):
        self.on_url = on_url
        self.on_exit = on_exit
        self.running = False
        self.port = None
        FakeQuickTunnel.instances.append(self)

    def start(self, binary, local_port):
        self.running = True
        self.port = local_port
        self.on_url(f"https://demo-{len(FakeQuickTunnel.instances)}.trycloudflare.com")

    def is_running(self):
        return self.running

    def stop(self):
        self.running = False
        self.on_exit(0)

    def crash(self):
        self.running = False
        self.on_exit(1)

@pytest.fixture
def fake_cloudflared(monkeypatch):
    FakeQuickTunnel.instances = []
    monkeypatch.setattr(cloudflared, "ensure_binary", lambda log=print: "cloudflared")
    monkeypatch.setattr(cloudflared, "QuickTunnel", FakeQuickTunnel)
    monkeypatch.setattr(common_module, "WEB_TUNNEL_RETRY_SEC", 0)
    yield FakeQuickTunnel.instances
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": False})

class RecordingProcess:
    """Stand-in for ServerProcess that the test can make exit with any code."""

    instances: list["RecordingProcess"] = []

    def __init__(self, args, cwd, on_output, on_exit):
        self.on_exit = on_exit
        self.running = False
        self.sent: list[str] = []
        RecordingProcess.instances.append(self)

    def start(self):
        self.running = True

    def is_running(self):
        return self.running

    def send(self, command):
        self.sent.append(command)

    def stop(self):
        self.running = False

    def kill(self):
        self.running = False

    def exit(self, code):
        self.running = False
        self.on_exit(code)

@pytest.fixture
def fake_server(monkeypatch):
    RecordingProcess.instances = []
    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: True)
    monkeypatch.setattr(java_manager, "ensure_java", lambda required, log=print: "java")
    monkeypatch.setattr(downloaders, "launch_args", lambda *args, **kwargs: ["java"])
    monkeypatch.setattr(common_module, "update_properties", lambda server_dir, **kwargs: None)
    monkeypatch.setattr(common_module, "ServerProcess", RecordingProcess)
    monkeypatch.setattr(common_module, "CRASH_RESTART_DELAY_SEC", 0)
    runtime = app.state.runtime
    runtime.process = None
    runtime.crash_times = []
    yield RecordingProcess.instances
    runtime.process = None
    runtime.crash_times = []

def _wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()

def _lifecycle_free():
    lock = app.state.runtime.lifecycle
    if not lock.acquire(blocking=False):
        return False
    lock.release()
    return True

def _wait_idle(timeout=3.0):
    """Wait for the background lifecycle job (create/start/restart/world switch) to finish."""
    assert _wait_until(_lifecycle_free, timeout), "lifecycle job did not finish"

def _start(**kwargs):
    response = client.post("/api/server/start", auth=("admin", "s3cret-pass"), **kwargs)
    _wait_idle()
    return response

def _make_backup(server_dir, name, members):
    path = os.path.join(server_dir, name)
    with zipfile.ZipFile(path, "w") as zf:
        for member in members:
            zf.writestr(member, "data")
    return path

@pytest.fixture
def sent_emails(monkeypatch):
    """Enable email alerts and capture what would be sent."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "send_email", lambda config, subject, body: sent.append((subject, body)))
    notifier.save_config({"email_enabled": True, "email_to": "me@example.com",
                          "smtp_user": "bot@gmail.com", "smtp_password": "abcd efgh"})
    yield sent

def _git(cwd, *args):
    import subprocess

    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)

def _commit_file(repo, name, content, message):
    with open(os.path.join(repo, name), "w", encoding="utf-8") as handle:
        handle.write(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)

@pytest.fixture
def git_repos(tmp_path, monkeypatch):
    """origin (bare) <- publisher clone pushes updates; app clone is what the updater manages."""
    from core import updater

    origin, publisher, app_dir = tmp_path / "origin.git", tmp_path / "publisher", tmp_path / "app"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(publisher))
    _commit_file(publisher, "requirements.txt", "requests\n", "initial")
    _git(publisher, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(app_dir))
    monkeypatch.setattr(updater, "REPO_DIR", str(app_dir))
    return {"publisher": str(publisher), "app": str(app_dir)}

def _publish(git_repos, name, content, message):
    _commit_file(git_repos["publisher"], name, content, message)
    _git(git_repos["publisher"], "push", "origin", "HEAD:main")

def _zip_bytes(entries):
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()

def _write_zip(path, entries):
    with open(path, "wb") as handle:
        handle.write(_zip_bytes(entries))
    return str(path)

@pytest.fixture
def fake_modrinth(monkeypatch):
    """A tiny fake of the Modrinth API: 'sodium' requires 'fabric-api'; downloads write small files."""
    from core import modrinth

    projects = {
        "sodium": {"id": "P1", "slug": "sodium", "title": "Sodium", "project_type": "mod", "body": "# Fast\n**Very** fast [docs](https://x)"},
        "P1": None,
        "fabric-api": {"id": "P2", "slug": "fabric-api", "title": "Fabric API", "project_type": "mod", "body": ""},
        "P2": None,
    }
    projects["P1"], projects["P2"] = projects["sodium"], projects["fabric-api"]
    versions = {
        "P1": [{"version_number": "0.6", "files": [{"url": "u1", "filename": "sodium.jar", "primary": True}],
                "dependencies": [{"dependency_type": "required", "project_id": "P2"},
                                 {"dependency_type": "optional", "project_id": "P9"}]}],
        "P2": [{"version_number": "0.100", "files": [{"url": "u2", "filename": "fabric-api.jar", "primary": True}],
                "dependencies": []}],
    }
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/search":
            return {"hits": [{"project_id": "P1", "slug": "sodium", "title": "Sodium", "server_side": "optional"}],
                    "total_hits": 1, "offset": 0, "limit": 20}
        if path == "/tag/category":
            return [{"name": "optimization", "project_type": "mod", "header": "categories"},
                    {"name": "16x", "project_type": "resourcepack", "header": "resolutions"}]
        if path.endswith("/version"):
            return versions[path.split("/")[2]]
        return projects[path.split("/")[2]]

    def fake_download(url, dest, log=print, progress=None):
        with open(dest, "w", encoding="utf-8") as handle:
            handle.write(url)

    monkeypatch.setattr(modrinth, "_get", fake_get)
    monkeypatch.setattr(modrinth, "download", fake_download)
    modrinth._categories_cache.update(at=0.0, items=[])
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "loader": "fabric", "version": "1.21.7"})
    app.state.runtime.process = None
    return calls

def _login(browser, password="s3cret-pass"):
    return browser.post("/api/login", json={"username": "admin", "password": password})

@pytest.fixture
def slow_install(fake_server, monkeypatch):
    """Installs block until the test sets the returned event."""
    gate = threading.Event()

    def prepare(loader, version, target_dir, java_exe, log=print, progress=None):
        if progress:
            progress(10, 100)
        if not gate.wait(5):
            raise RuntimeError("test gate never opened")

    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: False)
    monkeypatch.setattr(downloaders, "prepare", prepare)
    monkeypatch.setattr(common_module, "accept_eula", lambda target_dir: None)
    yield gate
    gate.set()
    _wait_idle()

players_route = importlib.import_module("webapp.routes.players")

AUTH = ("admin", "s3cret-pass")

def _server_file(name):
    return os.path.join(common_module._server_dir_for_config(common_module._effective_config()), name)

@pytest.fixture
def offline_network(monkeypatch):
    from core import netinfo
    monkeypatch.setattr(netinfo, "public_ip", lambda: "203.0.113.9")
    monkeypatch.setattr(netinfo, "lan_ip", lambda: "192.168.1.50")

def _enable_status_page():
    info = client.post("/api/status-page", auth=AUTH, json={"action": "enable"}).json()
    return info, info["url"].rsplit("/s/", 1)[1]

import hashlib  # noqa: E402

from core import modpack  # noqa: E402

@pytest.fixture
def fabric_server(monkeypatch):
    """A Fabric server dir with three jars and a faked Modrinth that knows two of them."""
    config_store.save({"loader": "fabric", "version": "1.21.1", "motd": "Our Pack", "online_mode": False})
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    mods = os.path.join(sdir, "mods")
    os.makedirs(mods, exist_ok=True)
    os.makedirs(os.path.join(sdir, "libraries", "net", "fabricmc", "fabric-loader", "0.16.10"), exist_ok=True)
    jars = {"sodium.jar": b"client and server", "lagfix.jar": b"server only", "homemade.jar": b"not on modrinth"}
    for name, content in jars.items():
        with open(os.path.join(mods, name), "wb") as handle:
            handle.write(content)
    sha1 = {name: hashlib.sha1(content).hexdigest() for name, content in jars.items()}
    calls = []

    def lookup(hashes):
        calls.append(sorted(hashes))
        known = {"sodium.jar": "P1", "lagfix.jar": "P2"}
        return {sha1[name]: {"project_id": pid, "files": [{"hashes": {"sha1": sha1[name]},
                                                           "url": f"https://cdn.modrinth.com/data/{pid}/{name}"}]}
                for name, pid in known.items()}

    monkeypatch.setattr(modpack, "_lookup", lookup)
    monkeypatch.setattr(modpack, "_projects", lambda ids: {
        "P1": {"id": "P1", "title": "Sodium", "client_side": "required", "server_side": "optional"},
        "P2": {"id": "P2", "title": "Lag Fix", "client_side": "unsupported", "server_side": "required"}})
    return {"sdir": sdir, "jars": jars, "calls": calls}

OWNER_HOOK = "https://discord.com/api/webhooks/111/owner-token"

FRIENDS_HOOK = "https://discord.com/api/webhooks/222/friends-token"

@pytest.fixture
def discord_posts(monkeypatch):
    """Capture Discord webhook posts: list of (url, content)."""
    posts: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "send_discord", lambda url, content: posts.append((url, content)))
    monkeypatch.setattr(common_module, "FRIENDS_BATCH_SEC", 0.05)
    return posts

def _hooks(**extra):
    return client.put("/api/notifications", auth=AUTH,
                      json={"discord_owner_url": OWNER_HOOK, "discord_friends_url": FRIENDS_HOOK, **extra})

import datetime  # noqa: E402

from core import backups as backups_core  # noqa: E402

webapp_module_backups = importlib.import_module("webapp.routes.backups")

def _world_dir(folder="world"):
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    os.makedirs(os.path.join(sdir, folder), exist_ok=True)
    with open(os.path.join(sdir, folder, "level.dat"), "wb") as handle:
        handle.write(b"level")
    return sdir

def _at(hhmm, day=22):
    hours, minutes = map(int, hhmm.split(":"))
    return datetime.datetime(2026, 10, day, hours, minutes, 30)

@pytest.fixture
def schedule_runtime():
    runtime = app.state.runtime
    runtime.daily_restart_done = ""
    runtime.restart_warnings_sent.clear()
    runtime.stopped_reason = ""
    runtime.online_players.clear()
    yield runtime
    runtime.online_players.clear()

import gzip  # noqa: E402

from core import diagnose  # noqa: E402

LOG_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "logs")

def _diagnose_fixture(name):
    with open(os.path.join(LOG_FIXTURES, name), encoding="utf-8") as handle:
        return diagnose.diagnose(handle.read().splitlines())

from core import mod_updates  # noqa: E402

@pytest.fixture
def outdated_mods(monkeypatch):
    """A Fabric server with an outdated recorded mod, an outdated uploaded jar and an unknown jar."""
    config_store.save({"loader": "fabric", "version": "1.21.1", "online_mode": False})
    sdir = _world_dir()
    mods = os.path.join(sdir, "mods")
    os.makedirs(mods, exist_ok=True)
    jars = {"sodium-0.5.jar": b"sodium old", "lithium-0.10.jar": b"lithium old", "homemade.jar": b"mine"}
    for name, content in jars.items():
        with open(os.path.join(mods, name), "wb") as handle:
            handle.write(content)
    from core import modrinth
    modrinth._save_record(mods, {"P1": {"slug": "sodium", "title": "Sodium", "filename": "sodium-0.5.jar", "version": "0.5"}})
    new_content = {"sodium-0.6.jar": b"sodium new", "lithium-0.11.jar": b"lithium new"}

    def version(pid, number, filename, deps=()):
        content = new_content[filename]
        return {"project_id": pid, "version_number": number, "changelog": f"## {number}\n- fixes",
                "dependencies": [{"project_id": d, "dependency_type": "required"} for d in deps],
                "files": [{"primary": True, "filename": filename, "url": f"https://cdn.modrinth.com/{filename}",
                           "size": len(content), "hashes": {"sha1": hashlib.sha1(content).hexdigest(),
                                                            "sha512": hashlib.sha512(content).hexdigest()}}]}

    requests_made = []

    class Reply:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def fake_post(url, headers, timeout, json):
        requests_made.append((url, json))
        sha = {hashlib.sha1(c).hexdigest(): n for n, c in jars.items()}
        answer = {}
        for h in json["hashes"]:
            if sha.get(h) == "sodium-0.5.jar":
                answer[h] = version("P1", "0.6", "sodium-0.6.jar", deps=["P9"])
            elif sha.get(h) == "lithium-0.10.jar":
                answer[h] = version("P2", "0.11", "lithium-0.11.jar")
        return Reply(answer)

    def fake_download(url, dest, log=print, progress=None):
        with open(dest, "wb") as handle:
            handle.write(new_content[url.rsplit("/", 1)[1]])
        return dest

    installed_deps = []
    monkeypatch.setattr(mod_updates.requests, "post", fake_post)
    monkeypatch.setattr(mod_updates, "download", fake_download)
    monkeypatch.setattr(modrinth, "install", lambda ref, loader, gv, dest, log=print: installed_deps.append(ref))
    return {"mods": mods, "requests": requests_made, "deps": installed_deps, "sdir": sdir}

from core import activity  # noqa: E402

T0 = 1_760_000_000.0

import struct  # noqa: E402

from webapp import properties_meta  # noqa: E402

def _png(width, height, padding=0):
    ihdr = struct.pack(">II", width, height) + bytes([8, 6, 0, 0, 0])
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0" * (4 + padding)

from core import storage as storage_core  # noqa: E402

@pytest.fixture
def storage_tree():
    """Active vanilla 1.21.7 server + an unused Fabric 1.20.1 one + two Java runtimes."""
    active = common_module._server_dir_for_config(common_module._effective_config())  # vanilla-1.21.7
    old = os.path.join(paths.SERVERS_DIR, "fabric-1.20.1")

    def write(path, size):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)

    write(os.path.join(active, "world", "level.dat"), 1000)
    write(os.path.join(active, "world-backup-world-20261001-100000-auto.zip"), 500)
    write(os.path.join(active, "world-pre-restore-20260901-120000", "level.dat"), 300)
    write(os.path.join(active, "installer.jar"), 200)
    write(os.path.join(active, "logs", "2026-01-01-1.log.gz"), 50)
    os.utime(os.path.join(active, "logs", "2026-01-01-1.log.gz"), (1, 1))
    write(os.path.join(old, "world", "level.dat"), 4000)
    write(os.path.join(paths.JAVA_DIR, "jre-21", "bin", "java"), 700)
    write(os.path.join(paths.JAVA_DIR, "jre-8", "bin", "java"), 600)
    return {"active": active, "old": old}


# Underscore helpers (_start, _wait_idle, ...) are shared too, so export everything.
__all__ = [name for name in dir() if not name.startswith("__")]
