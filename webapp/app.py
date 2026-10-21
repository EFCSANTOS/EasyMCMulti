from __future__ import annotations

import asyncio
import glob
import os
import time
import zipfile
from typing import Any

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - installed in project venv for runtime metrics
    psutil = None

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import config_store, downloaders, java_manager, modrinth, netinfo, paths, playit
from core.server_process import ServerProcess, accept_eula, update_properties
from webapp.auth import validate_auth_settings, verify_basic_auth_header, verify_credentials
from webapp.state import RuntimeState
from webapp.websocket_manager import WebSocketManager

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
RESTART_STOP_TIMEOUT_SEC = 60
FALLBACK_VERSIONS = {
    "vanilla": ["1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.20.4"],
    "paper": ["1.21.7", "1.21.6", "1.21.4", "1.21.1", "1.20.4"],
    "fabric": ["1.21.7", "1.21.6", "1.21.4", "1.20.4"],
    "forge": ["1.20.1", "1.19.2", "1.16.5"],
    "neoforge": ["1.21.7", "1.21.6", "1.21.1", "1.20.1"],
}

security = HTTPBasic()

def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    return verify_credentials(credentials)


def _server_properties_from_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "server-port": int(config.get("server_port", 25565)),
        "online-mode": bool(config.get("online_mode", True)),
        "max-players": int(config.get("max_players", 10)),
        "motd": str(config.get("motd", "EZ Minecraft Server")),
        "level-seed": str(config.get("level_seed", "")),
        "gamemode": str(config.get("gamemode", "survival")),
        "difficulty": str(config.get("difficulty", "normal")),
        "level-type": str(config.get("level_type", "normal")),
        "pvp": bool(config.get("pvp", True)),
        "view-distance": int(config.get("view_distance", 10)),
        "simulation-distance": int(config.get("simulation_distance", 10)),
        "spawn-monsters": bool(config.get("spawn_monsters", True)),
        "allow-nether": bool(config.get("allow_nether", True)),
        "allow-flight": bool(config.get("allow_flight", False)),
        "white-list": bool(config.get("white_list", False)),
        "enable-command-block": bool(config.get("enable_command_block", True)),
    }


def _default_config() -> dict[str, Any]:
    return {
        "loader": "vanilla",
        "version": "1.21.7",
        "ram_mb": 2048,
        "server_port": 25565,
        "motd": "EZ Minecraft Server",
        "max_players": 10,
        "gamemode": "survival",
        "difficulty": "normal",
        "level_type": "normal",
        "view_distance": 10,
        "simulation_distance": 10,
        "level_seed": "",
        "online_mode": True,
        "pvp": True,
        "spawn_monsters": True,
        "allow_flight": False,
        "allow_nether": True,
        "white_list": False,
        "enable_command_block": True,
    }


def _effective_config() -> dict[str, Any]:
    config = config_store.load()
    merged = _default_config()
    if isinstance(config, dict):
        merged.update(config)
    return merged


def _system_metrics_snapshot() -> dict[str, Any]:
    if psutil is not None:
        mem = psutil.virtual_memory()
        return {
            "cpu": float(psutil.cpu_percent(interval=None)),
            "ram": int(mem.used),
            "ramTotal": int(mem.total),
            "uptime": int(time.time() - _APP_START_TIME),
        }

    total = 1
    used = 0
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total = int(stat.ullTotalPhys)
            used = int(total - stat.ullAvailPhys)
    except Exception:
        total = 1
        used = 0

    return {
        "cpu": 0.0,
        "ram": int(used),
        "ramTotal": int(total),
        "uptime": 0,
    }


def _server_dir_for_config(config: dict[str, Any]) -> str:
    loader = str(config.get("loader", "vanilla"))
    version = str(config.get("version", "1.21.7"))
    return paths.server_dir(loader, version)


def _playit_on_exit(console_manager: Any, runtime: RuntimeState, code: int) -> None:
    runtime.playit_state = "stopped"
    runtime.playit_agent = None
    console_manager.publish(f"[playit] exited with code {code}")


def _playit_state(runtime: RuntimeState, cfg: dict[str, Any]) -> str:
    if runtime.playit_agent is not None and runtime.playit_agent.is_running():
        return "running"
    return "configured" if cfg.get("secret") else "not-configured"


def _is_backup_name(name: str) -> bool:
    return name.startswith("world-backup-") and name.endswith(".zip")


def _is_safe_world_member(member: str) -> bool:
    """True if a zip entry stays inside world/ (no absolute paths, drive letters or '..')."""
    normalized = member.replace("\\", "/")
    parts = normalized.split("/")
    return (
        parts[0] == "world"
        and not normalized.startswith("/")
        and ":" not in parts[0]
        and ".." not in parts
    )


def _wait_for_exit(process: Any, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while process.is_running():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


def _content_dir_for_config(config: dict[str, Any]) -> str:
    loader = str(config.get("loader", "vanilla"))
    folder = paths.content_folder(loader, _server_dir_for_config(config))
    if folder is None:
        raise HTTPException(status_code=400, detail=f"The {loader} loader does not support mods or plugins.")
    os.makedirs(folder, exist_ok=True)
    return folder


def _server_is_installed(target_dir: str, loader: str) -> bool:
    if loader in {"vanilla", "paper", "fabric"}:
        return os.path.exists(os.path.join(target_dir, "server.jar"))

    if loader in {"forge", "neoforge"}:
        return bool(
            glob.glob(os.path.join(target_dir, f"{loader}-*.jar"))
            or glob.glob(os.path.join(target_dir, f"{loader}*.jar"))
            or glob.glob(os.path.join(target_dir, "libraries", "**", "win_args.txt"), recursive=True)
        )
    return False


_DASHBOARD_NAVIGATION = (
    ("overview", "Overview"),
    ("setup", "Setup"),
    ("advanced", "Advanced"),
    ("mods", "Mods"),
    ("remote", "Remote"),
    ("backups", "Backups"),
    ("console", "Console"),
)

_SECTION_HERO = """
        <section id="overview" class="card hero">
          <div class="hero-text">
            <h1>Welcome back 👋</h1>
            <p>Manage your Minecraft server without leaving the dashboard.</p>
          </div>
          <div class="hero-status">
            <div class="status-dot" id="server-dot"></div>
            <div>
              <div class="status-label">Server status</div>
              <div id="status-panel" class="status-value">Loading...</div>
            </div>
          </div>
        </section>
"""

_SECTION_METRICS = """
        <section id="metrics" class="card metrics-panel">
          <div class="card-header">
            <h2>System resources</h2>
            <span id="metrics-badge" class="badge badge-live">● live</span>
          </div>

          <div class="metrics-grid">
            <div class="metric-card">
              <div class="metric-head">
                <div>
                  <div class="metric-label">CPU</div>
                  <div id="cpu-value" class="metric-value">—%</div>
                </div>
                <div id="cpu-sub" class="metric-sub">Waiting for data…</div>
              </div>
              <canvas id="cpu-chart" height="120"></canvas>
              <div class="metric-foot">
                <span>min <strong id="cpu-min">—</strong></span>
                <span>max <strong id="cpu-max">—</strong></span>
                <span>avg <strong id="cpu-avg">—</strong></span>
              </div>
            </div>

            <div class="metric-card">
              <div class="metric-head">
                <div>
                  <div class="metric-label">RAM</div>
                  <div id="ram-value" class="metric-value">—</div>
                </div>
                <div id="ram-sub" class="metric-sub">Waiting for data…</div>
              </div>
              <canvas id="ram-chart" height="120"></canvas>
              <div class="metric-foot">
                <span>min <strong id="ram-min">—</strong></span>
                <span>max <strong id="ram-max">—</strong></span>
                <span>avg <strong id="ram-avg">—</strong></span>
              </div>
            </div>
          </div>

          <div class="metrics-meta">
            <span id="metrics-endpoint">endpoint: <code>—</code></span>
            <span id="metrics-last">last update: —</span>
            <button id="metrics-toggle" class="btn btn-ghost" type="button">Pause</button>
          </div>
        </section>
"""

_SECTION_SETUP = """
        <section id="setup" class="card">
          <div class="card-header">
            <h2>Server setup</h2>
          </div>
          <form id="setup-form" class="form-grid">
            <div>
              <label for="loader-select">Loader</label>
              <select id="loader-select" name="loader">
                <option value="vanilla">vanilla</option>
                <option value="paper">paper</option>
                <option value="fabric">fabric</option>
                <option value="forge">forge</option>
                <option value="neoforge">neoforge</option>
              </select>
            </div>
            <div>
              <label for="version-select">Version</label>
              <select id="version-select" name="version"></select>
            </div>
            <div>
              <label for="ram-input">RAM (MB)</label>
              <input id="ram-input" name="ram_mb" type="number" min="512" step="256" value="2048" />
            </div>
            <div>
              <label for="port-input">Port</label>
              <input id="port-input" name="server_port" type="number" min="1024" max="65535" value="25565" />
            </div>
            <button type="submit" class="btn btn-primary">Save config</button>
            <button id="create-button" type="button" class="btn btn-secondary">Create server</button>
          </form>
        </section>
"""

_SECTION_ADVANCED = """
        <section id="advanced" class="card">
          <div class="card-header">
            <h2>Advanced settings</h2>
          </div>
          <div class="form-grid advanced-grid">
            <div>
              <label for="motd-input">MOTD</label>
              <input id="motd-input" value="EZ Minecraft Server" />
            </div>
            <div>
              <label for="maxplayers-input">Max players</label>
              <input id="maxplayers-input" type="number" min="1" max="1000" value="10" />
            </div>
            <div>
              <label for="gamemode-input">Game mode</label>
              <select id="gamemode-input">
                <option value="survival">survival</option>
                <option value="creative">creative</option>
                <option value="adventure">adventure</option>
                <option value="spectator">spectator</option>
              </select>
            </div>
            <div>
              <label for="difficulty-input">Difficulty</label>
              <select id="difficulty-input">
                <option value="peaceful">peaceful</option>
                <option value="easy">easy</option>
                <option value="normal">normal</option>
                <option value="hard">hard</option>
              </select>
            </div>
            <div>
              <label for="leveltype-input">World type</label>
              <select id="leveltype-input">
                <option value="normal">normal</option>
                <option value="flat">flat</option>
                <option value="largebiomes">largebiomes</option>
                <option value="amplified">amplified</option>
              </select>
            </div>
            <div>
              <label for="viewdist-input">View distance</label>
              <input id="viewdist-input" type="number" min="3" max="32" value="10" />
            </div>
            <div>
              <label for="simdist-input">Simulation distance</label>
              <input id="simdist-input" type="number" min="3" max="32" value="10" />
            </div>
            <div>
              <label for="seed-input">Seed</label>
              <input id="seed-input" value="" placeholder="empty = random" />
            </div>
          </div>

          <div class="checkbox-grid">
            <label><input id="online-mode" type="checkbox" checked /> Online mode</label>
            <label><input id="pvp-mode" type="checkbox" checked /> PvP</label>
            <label><input id="spawn-monsters" type="checkbox" checked /> Spawn monsters</label>
            <label><input id="allow-flight" type="checkbox" /> Allow flight</label>
            <label><input id="nether-mode" type="checkbox" checked /> Nether</label>
            <label><input id="whitelist-mode" type="checkbox" /> Whitelist</label>
            <label><input id="command-blocks" type="checkbox" checked /> Command blocks</label>
          </div>
          <div class="card-row">
            <button id="advanced-save-button" class="btn btn-primary" type="button">Save settings</button>
          </div>
        </section>
"""

_SECTION_CONTROLS = """
        <section class="card">
          <div class="card-header">
            <h2>Server controls</h2>
          </div>
          <div class="btn-grid">
            <button id="start-button" class="btn btn-primary" type="button">Start server</button>
            <button id="stop-button" class="btn btn-danger" type="button">Stop server</button>
            <button id="restart-button" class="btn btn-secondary" type="button">Restart server</button>
            <button id="backup-button" class="btn btn-secondary" type="button">Backup world</button>
          </div>
        </section>
"""

_SECTION_COMMAND = """
        <section id="command-form-block" class="card">
          <div class="card-header">
            <h2>Console command</h2>
          </div>
          <form id="command-form" class="inline-form">
            <input id="command-input" name="command" placeholder="say hello" />
            <button type="submit" class="btn btn-primary">Send</button>
          </form>
        </section>
"""

_SECTION_REMOTE = """
        <section id="remote" class="card">
          <div class="card-header">
            <h2>Remote access</h2>
          </div>
          <div class="btn-grid">
            <button id="playit-config-button" class="btn btn-secondary" type="button">Configure tunnel</button>
            <button id="playit-start-button" class="btn btn-secondary" type="button">Start tunnel</button>
            <button id="playit-stop-button" class="btn btn-danger" type="button">Stop tunnel</button>
          </div>
          <div class="status-block" id="playit-status-panel">PlayIt not configured.</div>
        </section>
"""

_SECTION_MODS = """
        <section id="mods" class="card">
          <div class="card-header">
            <h2>Mods / Plugins</h2>
          </div>
          <div class="inline-form compact">
            <input id="mod-search-query" placeholder="Search Modrinth" />
            <button id="mod-search-button" class="btn btn-secondary" type="button">Search</button>
            <button id="mod-refresh-button" class="btn btn-ghost" type="button">Refresh</button>
          </div>
          <div class="inline-form compact">
            <input id="mod-upload-input" type="file" multiple accept=".jar,application/java-archive" />
            <button id="mod-upload-button" class="btn btn-secondary" type="button">Upload jars</button>
          </div>
          <div class="split">
            <div>
              <label for="mod-search-results">Search results</label>
              <select id="mod-search-results" size="6" class="listbox"></select>
            </div>
            <div>
              <label for="mod-installed-list">Installed</label>
              <select id="mod-installed-list" size="6" class="listbox"></select>
            </div>
          </div>
          <div class="card-row">
            <button id="mod-install-button" class="btn btn-primary" type="button">Install selected</button>
          </div>
        </section>
"""

_SECTION_ADMIN = """
        <section id="admin" class="card">
          <div class="card-header">
            <h2>Admin commands</h2>
          </div>
          <div class="inline-form compact">
            <input id="admin-player" placeholder="Player name" />
            <button id="admin-op" class="btn btn-secondary" type="button">OP</button>
            <button id="admin-deop" class="btn btn-secondary" type="button">De-OP</button>
            <button id="admin-kick" class="btn btn-secondary" type="button">Kick</button>
            <button id="admin-ban" class="btn btn-secondary" type="button">Ban</button>
          </div>
          <div class="btn-grid small-grid">
            <button id="admin-day" class="btn btn-ghost" type="button">Day</button>
            <button id="admin-night" class="btn btn-ghost" type="button">Night</button>
            <button id="admin-clear" class="btn btn-ghost" type="button">Clear weather</button>
            <button id="admin-rain" class="btn btn-ghost" type="button">Rain</button>
            <button id="admin-save" class="btn btn-ghost" type="button">Save all</button>
          </div>
        </section>
"""

_SECTION_BACKUPS = """
        <section id="backups" class="card">
          <div class="card-header">
            <h2>Backups</h2>
          </div>
          <div class="inline-form compact">
            <select id="backup-list" class="listbox" size="5"></select>
            <button id="backup-restore-button" class="btn btn-secondary" type="button">Restore selected</button>
          </div>
        </section>
"""

_SECTION_CONSOLE = """
        <section id="console" class="card">
          <div class="card-header">
            <h2>Console</h2>
          </div>
          <pre id="console-output">Waiting for logs...</pre>
        </section>
"""

# Each dashboard page is a list of sections; the overview shows everything.
_DASHBOARD_PAGES: dict[str, tuple[str, ...]] = {
    "overview": (
        _SECTION_HERO, _SECTION_METRICS, _SECTION_SETUP, _SECTION_ADVANCED, _SECTION_CONTROLS,
        _SECTION_COMMAND, _SECTION_REMOTE, _SECTION_MODS, _SECTION_ADMIN, _SECTION_BACKUPS,
        _SECTION_CONSOLE,
    ),
    "setup": (_SECTION_SETUP,),
    "advanced": (_SECTION_ADVANCED, _SECTION_CONTROLS, _SECTION_COMMAND),
    "mods": (_SECTION_MODS, _SECTION_ADMIN),
    "remote": (_SECTION_REMOTE,),
    "backups": (_SECTION_BACKUPS,),
    "console": (_SECTION_CONSOLE, _SECTION_COMMAND),
}


def _render_dashboard_page(section: str) -> HTMLResponse:
    normalized = section.strip("/").lower()
    if normalized not in _DASHBOARD_PAGES:
        raise HTTPException(status_code=404, detail=f"Dashboard section '{section}' not found.")

    nav_html = "".join(
        f'<a href="/dashboard/{key}" class="nav-link{" active" if key == normalized else ""}">{label}</a>'
        for key, label in _DASHBOARD_NAVIGATION
    )
    body_html = "".join(_DASHBOARD_PAGES[normalized])

    return HTMLResponse(
        f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>EZ Minecraft Server Dashboard</title>
    <link rel="stylesheet" href="/dashboard.css" />
  </head>
  <body>
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">⛏</div>
          <div>
            <div class="brand-title">EZ Minecraft</div>
            <div class="brand-sub">Server Dashboard</div>
          </div>
        </div>

        <nav class="nav" aria-label="Dashboard sections">{nav_html}</nav>

        <div class="sidebar-footer">
          <div class="status-pill" id="session-status">Signed in</div>
          <button id="logout-button" class="btn btn-ghost" type="button">Sign out</button>
        </div>
      </aside>

      <main class="main">
        <div id="action-status" class="card action-status" role="status" aria-live="polite" hidden></div>
        {body_html}
      </main>
    </div>
    <script src="/dashboard.js"></script>
  </body>
</html>
        """
    )


_APP_START_TIME = time.time()


def create_app() -> FastAPI:
    validate_auth_settings()
    app = FastAPI(title="EZ Minecraft Server Launcher", version="0.1.0")
    runtime = RuntimeState()
    app.state.runtime = runtime
    app.state.console_manager = WebSocketManager()

    def append_console_line(line: str) -> None:
        app.state.console_manager.publish(line)

    def update_install_state(status: str, progress: float, message: str) -> None:
        runtime.install_status = status
        runtime.install_progress = max(0.0, min(float(progress), 100.0))
        runtime.install_message = message

    def ensure_server_installed(config: dict[str, Any]) -> str:
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_dir = _server_dir_for_config(config)
        if _server_is_installed(target_dir, loader):
            update_install_state("ready", 100.0, f"{loader} {version} ready.")
            return target_dir

        runtime.state = "installing"
        runtime.operation = "installing"
        update_install_state("installing", 0.0, f"Installing {loader} {version}...")

        def install_log(message: str) -> None:
            if message:
                append_console_line(f"[install] {message}")

        def install_progress(done: int, total: int) -> None:
            if total > 0:
                update_install_state("installing", (done / total) * 100.0, f"Downloading {loader} {version} ({done}/{total} bytes)")
            else:
                update_install_state("installing", 100.0, f"Installing {loader} {version}...")

        try:
            java_exe = java_manager.ensure_java(java_manager.required_java(version))
            downloaders.prepare(loader, version, target_dir, java_exe, log=install_log, progress=install_progress)
            accept_eula(target_dir)
        except Exception as exc:
            runtime.state = "stopped"
            runtime.operation = "idle"
            update_install_state("failed", 0.0, str(exc))
            raise

        runtime.state = "stopped"
        runtime.operation = "idle"
        update_install_state("ready", 100.0, f"{loader} {version} installed.")
        return target_dir

    @app.get("/")
    def root() -> HTMLResponse:
        return _render_dashboard_page("overview")

    @app.get("/login")
    def login() -> HTMLResponse:
        template_path = os.path.join(os.path.dirname(__file__), "templates", "login.html")
        with open(template_path, "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())

    @app.get("/dashboard")
    def dashboard() -> RedirectResponse:
        return RedirectResponse("/dashboard/overview")

    @app.get("/dashboard/{section}")
    def dashboard_section(section: str) -> HTMLResponse:
        return _render_dashboard_page(section)

    @app.get("/dashboard.css")
    def dashboard_css() -> FileResponse:
        return FileResponse(os.path.join(os.path.dirname(__file__), "static", "css", "dashboard.css"))

    @app.get("/dashboard.js")
    def dashboard_js() -> FileResponse:
        return FileResponse(os.path.join(os.path.dirname(__file__), "static", "js", "dashboard.js"))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "ez-server-webapp"}

    @app.get("/api/system/metrics")
    @app.get("/api/system")
    @app.get("/api/stats")
    @app.get("/api/metrics")
    @app.get("/metrics")
    def system_metrics(_: str = Depends(require_auth)) -> dict[str, Any]:
        return _system_metrics_snapshot()

    @app.get("/api/status")
    def status(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        runtime.loader = str(config.get("loader", runtime.loader))
        runtime.version = str(config.get("version", runtime.version))
        runtime.lan_address = netinfo.lan_ip()
        runtime.public_address = netinfo.public_ip()
        playit_cfg = playit.load_config()
        runtime.playit_state = _playit_state(runtime, playit_cfg)
        runtime.playit_address = playit_cfg.get("address") or runtime.public_address
        return runtime.snapshot()

    @app.get("/api/console")
    def console_history(_: str = Depends(require_auth)) -> dict[str, Any]:
        return {"lines": list(app.state.console_manager.history)}

    @app.get("/api/config")
    def get_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return _effective_config()

    @app.put("/api/config")
    def save_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        config.update(payload)
        config_store.save(config)
        if "loader" in config:
            runtime.loader = str(config["loader"])
        if "version" in config:
            runtime.version = str(config["version"])
        return config

    @app.get("/api/versions/{loader}")
    def versions(loader: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        if loader not in KNOWN_LOADERS:
            raise HTTPException(status_code=400, detail=f"Unsupported loader: {loader}")

        try:
            versions = downloaders.list_versions(loader, include_snapshots=False)
        except Exception:
            versions = FALLBACK_VERSIONS.get(loader, [])

        if not versions:
            raise HTTPException(status_code=502, detail=f"No versions available for loader: {loader}")

        return {"loader": loader, "versions": versions}

    @app.post("/api/server/create")
    def create_server(payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        if payload:
            config.update(payload)
            config_store.save(config)
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        runtime.loader = loader
        runtime.version = version
        runtime.operation = "creating"
        runtime.state = "ready"
        try:
            target_dir = ensure_server_installed(config)
            update_properties(target_dir, **_server_properties_from_config(config))
        except Exception as exc:
            runtime.operation = "idle"
            runtime.state = "stopped"
            raise HTTPException(status_code=500, detail=f"Server creation failed: {exc}") from exc

        runtime.operation = "idle"
        runtime.state = "stopped"
        return {"state": runtime.state, "operation": runtime.operation, "loader": loader, "version": version, "server_dir": target_dir}

    @app.post("/api/server/start")
    def start_server(payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}

        config = _effective_config()
        if payload:
            config.update(payload)
            config_store.save(config)
        loader = str(config.get("loader", runtime.loader))
        version = str(config.get("version", runtime.version))
        runtime.loader = loader
        runtime.version = version
        runtime.state = "starting"
        runtime.operation = "starting"

        process: Any = None

        def on_exit(code: int) -> None:
            append_console_line(f"[server] exited with code {code}")
            if runtime.process is not process:
                return  # a newer process already replaced this one (e.g. after a restart)
            runtime.state = "stopped"
            runtime.operation = "idle"
            runtime.process = None
            runtime.clear_players()

        def on_output(line: str) -> None:
            runtime.track_player_event(line)
            append_console_line(line)

        target_dir = ensure_server_installed(config)
        update_properties(target_dir, **_server_properties_from_config(config))

        java_exe = java_manager.ensure_java(java_manager.required_java(version))
        args = downloaders.launch_args(loader, java_exe, target_dir, int(config.get("ram_mb", 2048)))
        process = ServerProcess(
            args,
            cwd=target_dir,
            on_output=on_output,
            on_exit=on_exit,
        )
        runtime.clear_players()
        runtime.process = process
        process.start()
        runtime.state = "running"
        runtime.operation = "idle"
        append_console_line(f"[server] started with {loader} {version}")
        return {"state": runtime.state, "operation": runtime.operation, "message": "Server started."}

    @app.post("/api/server/stop")
    def stop_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is None or not runtime.process.is_running():
            runtime.state = "stopped"
            runtime.operation = "idle"
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server is already stopped."}

        runtime.state = "stopping"
        runtime.operation = "stopping"
        runtime.process.stop()
        return {"state": runtime.state, "operation": runtime.operation, "message": "Stop requested."}

    @app.post("/api/server/restart")
    def restart_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        process = runtime.process
        stop_server(_)
        if process is not None and not _wait_for_exit(process, RESTART_STOP_TIMEOUT_SEC):
            append_console_line("[server] did not stop in time; killing the process")
            process.kill()
            _wait_for_exit(process, 10)
        return start_server(None, _)

    @app.post("/api/server/command")
    def send_command(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        command = str(payload.get("command", "")).strip()
        if not command:
            raise HTTPException(status_code=400, detail="A command is required.")
        if runtime.process is None or not runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Server is not running.")
        runtime.process.send(command)
        append_console_line(f"> {command}")
        return {"ok": True, "command": command}

    @app.post("/api/server/backup")
    def backup_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        world_dir = os.path.join(target_dir, "world")
        if not os.path.isdir(world_dir):
            return {"ok": False, "message": "No world directory found for backup."}
        archive = os.path.join(target_dir, f"world-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(world_dir):
                for name in files:
                    abs_path = os.path.join(root, name)
                    rel_path = os.path.relpath(abs_path, target_dir)
                    zf.write(abs_path, rel_path)
        return {"ok": True, "archive": archive, "message": f"Backup created: {os.path.basename(archive)}"}

    @app.get("/api/server/backups")
    def list_backups(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        backups = []
        if os.path.isdir(target_dir):
            for entry in sorted(os.listdir(target_dir), reverse=True):
                if _is_backup_name(entry):
                    backups.append(os.path.join(target_dir, entry))
        return {"backups": backups}

    @app.post("/api/server/backups/restore")
    def restore_backup(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Stop the server before restoring a backup.")
        # Only this server's own backups are accepted; a full path from the list is reduced to its name.
        name = os.path.basename(str(payload.get("backup_path", "") or payload.get("name", "")).strip())
        target_dir = _server_dir_for_config(_effective_config())
        backup_path = os.path.join(target_dir, name)
        if not (_is_backup_name(name) and os.path.isfile(backup_path)):
            raise HTTPException(status_code=400, detail="A valid backup from the backup list is required.")

        with zipfile.ZipFile(backup_path, "r") as zf:
            members = zf.namelist()
            unsafe = [m for m in members if not _is_safe_world_member(m)]
            if unsafe:
                raise HTTPException(status_code=400, detail=f"Backup contains files outside world/: {unsafe[0]}")
            world_dir = os.path.join(target_dir, "world")
            if os.path.isdir(world_dir):
                backup_name = f"world-backup-pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
                os.rename(world_dir, os.path.join(target_dir, backup_name))
            zf.extractall(target_dir, members=members)
        return {"ok": True, "message": f"Backup restored from {name}"}

    @app.post("/api/backups/restore")
    def legacy_restore_backup(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        return restore_backup(payload, _)

    @app.get("/api/backups")
    def legacy_list_backups(_: str = Depends(require_auth)) -> dict[str, Any]:
        return list_backups(_)

    @app.get("/api/server/mods")
    def list_mods(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        mods_dir = os.path.join(target_dir, "mods")
        plugins_dir = os.path.join(target_dir, "plugins")
        items = []
        for base_dir in (mods_dir, plugins_dir):
            if not os.path.isdir(base_dir):
                continue
            for name in sorted(os.listdir(base_dir)):
                if name.lower().endswith(".jar"):
                    items.append({"name": name, "path": os.path.join(base_dir, name)})
        return {"mods": items}

    @app.post("/api/server/mods/upload")
    def upload_mods(files: list[UploadFile] = File(default=[]), _: str = Depends(require_auth)) -> dict[str, Any]:
        if not files:
            raise HTTPException(status_code=400, detail="No mod files were uploaded.")
        mod_target = _content_dir_for_config(_effective_config())
        saved = []
        for upload in files:
            name = os.path.basename(upload.filename or "")
            if not name.lower().endswith(".jar"):
                continue
            dest = os.path.join(mod_target, name)
            with open(dest, "wb") as handle:
                while True:
                    chunk = upload.file.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
            saved.append(dest)
        if not saved:
            raise HTTPException(status_code=400, detail="Only .jar files can be uploaded.")
        return {"ok": True, "saved": saved, "message": f"Uploaded {len(saved)} file(s)."}

    @app.get("/api/mods")
    def legacy_list_mods(_: str = Depends(require_auth)) -> dict[str, Any]:
        return list_mods(_)

    @app.post("/api/admin/{action}")
    def admin_action(action: str, payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is None or not runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Server is not running.")

        action = action.lower()
        player = str((payload or {}).get("player", "")).strip() if payload else ""
        command_map = {
            "op": f"op {player}" if player else "op",
            "deop": f"deop {player}" if player else "deop",
            "kick": f"kick {player}" if player else "kick",
            "ban": f"ban {player}" if player else "ban",
            "day": "time set day",
            "night": "time set night",
            "clear": "weather clear",
            "rain": "weather rain",
            "save": "save-all",
        }
        if action not in command_map:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")

        command = command_map[action]
        if action in {"op", "deop", "kick", "ban"} and not player:
            raise HTTPException(status_code=400, detail="A player name is required for this action.")

        runtime.process.send(command)
        append_console_line(f"> {command}")
        return {"ok": True, "action": action, "command": command, "message": f"{action} command sent."}

    @app.get("/api/modrinth/search")
    def modrinth_search(query: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        term = (query or "").strip()
        if not term:
            raise HTTPException(status_code=400, detail="A Modrinth search query is required.")
        config = _effective_config()
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        items = modrinth.search(term, loader, version, limit=10)
        return {"items": items}

    @app.post("/api/modrinth/install")
    def modrinth_install(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        project_ref = str(payload.get("slug") or payload.get("project_id") or payload.get("id") or "").strip()
        if not project_ref:
            raise HTTPException(status_code=400, detail="A Modrinth project slug or ID is required.")
        config = _effective_config()
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_folder = _content_dir_for_config(config)
        installed = modrinth.install(project_ref, loader, version, target_folder, log=append_console_line)
        return {"ok": True, "filename": installed, "message": f"Installed {installed}."}

    @app.get("/api/playit/status")
    def playit_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        state = _playit_state(runtime, cfg)
        return {
            "configured": bool(cfg.get("secret")),
            "state": state,
            "address": cfg.get("address") or "",
            "secret_present": bool(cfg.get("secret")),
        }

    @app.post("/api/playit/configure")
    def configure_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        if cfg.get("secret"):
            runtime.playit_state = "configured"
            runtime.playit_address = cfg.get("address") or runtime.playit_address
            return {
                "configured": True,
                "state": "configured",
                "address": cfg.get("address") or "",
                "message": "PlayIt already configured.",
            }

        secret = playit.claim_interactive(append_console_line, lambda: False)
        playit.ensure_agent(log=append_console_line)
        runtime.playit_agent = playit.PlayitAgent(on_output=append_console_line, on_exit=lambda code: _playit_on_exit(app.state.console_manager, runtime, code))
        runtime.playit_agent.start(secret)
        local_port = int(_effective_config().get("server_port", 25565))
        address = playit.ensure_tunnel(secret, local_port, append_console_line)
        playit.save_config(secret, address)
        runtime.playit_state = "running"
        runtime.playit_address = address
        return {
            "configured": True,
            "state": "running",
            "address": address,
            "message": "PlayIt tunnel configured and running.",
        }

    @app.post("/api/playit/start")
    def start_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        if not cfg.get("secret"):
            return {"configured": False, "state": "not-configured", "address": "", "message": "PlayIt is not configured yet. Click Configure tunnel first."}
        if runtime.playit_agent and runtime.playit_agent.is_running():
            runtime.playit_state = "running"
            return {"configured": True, "state": "running", "address": cfg.get("address") or "", "message": "PlayIt tunnel is already running."}

        playit.ensure_agent(log=append_console_line)
        runtime.playit_agent = playit.PlayitAgent(on_output=append_console_line, on_exit=lambda code: _playit_on_exit(app.state.console_manager, runtime, code))
        runtime.playit_agent.start(cfg["secret"])
        local_port = int(_effective_config().get("server_port", 25565))
        address = playit.ensure_tunnel(cfg["secret"], local_port, append_console_line)
        playit.save_config(cfg["secret"], address)
        runtime.playit_state = "running"
        runtime.playit_address = address
        return {"configured": True, "state": "running", "address": address, "message": "PlayIt tunnel started."}

    @app.post("/api/playit/stop")
    def stop_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.playit_agent and runtime.playit_agent.is_running():
            runtime.playit_agent.stop()
        runtime.playit_agent = None
        cfg = playit.load_config()
        runtime.playit_state = "stopped"
        runtime.playit_address = cfg.get("address") or runtime.playit_address
        return {"configured": bool(cfg.get("secret")), "state": "stopped", "address": cfg.get("address") or "", "message": "PlayIt tunnel stopped."}

    @app.get("/api/playit/config")
    def playit_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        # Never expose the secret key over HTTP; it stays in playit_config.json on the host.
        cfg = playit.load_config()
        return {"address": cfg.get("address") or "", "secret_present": bool(cfg.get("secret"))}

    @app.websocket("/ws/console")
    async def console_websocket(websocket: WebSocket) -> None:
        auth_header = websocket.headers.get("authorization")
        try:
            verify_basic_auth_header(auth_header)
        except HTTPException:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        await app.state.console_manager.connect(websocket)
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    break
        finally:
            await app.state.console_manager.disconnect(websocket)

    return app


app = create_app()
