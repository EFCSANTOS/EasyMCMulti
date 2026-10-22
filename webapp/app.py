from __future__ import annotations

import asyncio
import glob
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import os
import threading
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

from core import cloudflared, config_store, downloaders, java_manager, modrinth, netinfo, paths, playit
from core.server_process import ServerProcess, accept_eula, update_properties
from webapp.auth import validate_auth_settings, verify_basic_auth_header, verify_credentials
from webapp.state import RuntimeState
from webapp.websocket_manager import WebSocketManager

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
RESTART_STOP_TIMEOUT_SEC = 60
SHUTDOWN_STOP_TIMEOUT_SEC = 45
# Crash auto-restart: wait CRASH_RESTART_DELAY_SEC, at most CRASH_RESTART_LIMIT times per CRASH_WINDOW_SEC.
CRASH_RESTART_DELAY_SEC = 10
CRASH_RESTART_LIMIT = 3
CRASH_WINDOW_SEC = 600
# Port uvicorn listens on; run.bat / run.sh pass the same value. Used for the remote dashboard tunnel.
WEB_PORT = int(os.getenv("EZ_WEBAPP_PORT", "8000"))
WEB_TUNNEL_RETRY_SEC = 15
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
        # Always-on behaviour (Server setup page).
        "autostart_server": False,
        "autostart_playit": False,
        "auto_restart": True,
        # Cloudflare link to the dashboard (Remote access page); restored on dashboard start.
        "remote_dashboard": False,
    }


def _effective_config() -> dict[str, Any]:
    config = config_store.load()
    merged = _default_config()
    if isinstance(config, dict):
        merged.update(config)
    return merged


def _system_metrics_snapshot() -> dict[str, Any]:
    # Each source is tried separately: Android blocks /proc/stat (CPU) but allows /proc/meminfo.
    cpu = 0.0
    if psutil is not None:
        try:
            cpu = float(psutil.cpu_percent(interval=None))
        except Exception:
            cpu = 0.0
    used, total = _memory_usage()
    return {
        "cpu": cpu,
        "ram": int(used),
        "ramTotal": int(total),
        "uptime": int(time.time() - _APP_START_TIME),
    }


def _memory_usage() -> tuple[int, int]:
    """(used, total) bytes from psutil, /proc/meminfo or the Windows API; (0, 1) if unknown."""
    if psutil is not None:
        try:
            mem = psutil.virtual_memory()
            return int(mem.used), int(mem.total)
        except Exception:
            pass

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            info = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in handle if line.split()[1:2]}
        total = info["MemTotal"]
        return total - info.get("MemAvailable", info.get("MemFree", 0)), total
    except (OSError, KeyError, ValueError, IndexError):
        pass

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
    return used, total


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
            or downloaders._find_args_file(target_dir)
        )
    return False


_ICON_PATHS = {
    "overview": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/>'
                '<rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "setup": '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/>'
             '<line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
    "advanced": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
                '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
                '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
                '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "mods": '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>'
            '<polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
    "remote": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>'
              '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "backups": '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/>'
               '<line x1="10" y1="12" x2="14" y2="12"/>',
    "console": '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "restart": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "archive": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "arrow": '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
}


def _icon(name: str) -> str:
    return f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">{_ICON_PATHS[name]}</svg>'


def _copy_button(field: str, label: str) -> str:
    return (f'<button class="icon-btn" type="button" data-copy-field="{field}" '
            f'title="Copy {label}" aria-label="Copy {label}">{_icon("copy")}</button>')


# (route key, nav label, page title, page subtitle)
_DASHBOARD_NAVIGATION = (
    ("overview", "Overview", "Overview", "Your server at a glance."),
    ("setup", "Server", "Server setup", "Choose the loader, version and resources, then create the server."),
    ("advanced", "Settings", "World settings", "Gameplay and world options written to server.properties."),
    ("mods", "Mods", "Mods & plugins", "Find mods on Modrinth or upload your own .jar files."),
    ("remote", "Remote access", "Remote access", "Let friends outside your network join."),
    ("backups", "Backups", "Backups", "Snapshot the world and roll back when needed."),
    ("console", "Console", "Console", "Live server output, commands and admin shortcuts."),
)

_SECTION_OVERVIEW_TILES = f"""
        <div id="install-banner" class="card install-banner" hidden>
          <div class="install-head">
            <strong>Installing server</strong>
            <span data-field="install_message" class="muted"></span>
          </div>
          <div class="progress"><div id="install-progress-bar" class="progress-bar"></div></div>
        </div>

        <section id="overview" class="tiles">
          <article class="card tile">
            <div class="tile-label">{_icon("setup")} Server</div>
            <div class="tile-value" data-field="server_name">—</div>
            <div class="tile-sub" data-field="server_meta">—</div>
            <div class="tile-actions">
              <button id="backup-button" class="btn btn-ghost btn-sm" type="button">{_icon("archive")} Backup world</button>
            </div>
          </article>
          <article class="card tile">
            <div class="tile-label">{_icon("users")} Players online</div>
            <div class="tile-value" data-field="players">0</div>
            <div class="tile-sub" data-field="player_names">No one online</div>
          </article>
          <article class="card tile">
            <div class="tile-label">{_icon("remote")} Join address</div>
            <div class="address-row"><span class="address-kind">LAN</span>
              <code data-field="lan_join">—</code>{_copy_button("lan_join", "LAN address")}</div>
            <div class="address-row"><span class="address-kind">Internet</span>
              <code data-field="public_join">—</code>{_copy_button("public_join", "internet address")}</div>
          </article>
        </section>
"""

_SECTION_METRICS = """
        <section id="metrics" class="card">
          <div class="card-header">
            <div>
              <h2>System resources</h2>
              <p class="card-sub">Host machine, sampled every 2 seconds.</p>
            </div>
            <div class="card-tools">
              <span id="metrics-badge" class="badge badge-live">live</span>
              <button id="metrics-toggle" class="btn btn-ghost btn-sm" type="button">Pause</button>
            </div>
          </div>
          <div class="metrics-grid">
            <div class="metric-card">
              <div class="metric-head">
                <div class="metric-label">CPU usage</div>
                <div id="cpu-sub" class="metric-sub">waiting</div>
              </div>
              <div id="cpu-value" class="metric-value">—</div>
              <div class="chart-wrap">
                <canvas id="cpu-chart" height="120" aria-label="CPU usage over the last two minutes" role="img"></canvas>
                <div class="chart-tip" hidden></div>
              </div>
              <div class="metric-foot">
                <span>min <strong id="cpu-min">—</strong></span>
                <span>avg <strong id="cpu-avg">—</strong></span>
                <span>max <strong id="cpu-max">—</strong></span>
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-head">
                <div class="metric-label">Memory used</div>
                <div id="ram-sub" class="metric-sub">waiting</div>
              </div>
              <div id="ram-value" class="metric-value">—</div>
              <div class="chart-wrap">
                <canvas id="ram-chart" height="120" aria-label="Memory usage over the last two minutes" role="img"></canvas>
                <div class="chart-tip" hidden></div>
              </div>
              <div class="metric-foot">
                <span>min <strong id="ram-min">—</strong></span>
                <span>avg <strong id="ram-avg">—</strong></span>
                <span>max <strong id="ram-max">—</strong></span>
              </div>
            </div>
          </div>
        </section>
"""

_COMMAND_FORM = """
          <form id="command-form" class="command-form">
            <span class="prompt" aria-hidden="true">&gt;</span>
            <input id="command-input" name="command" placeholder="Type a server command, e.g. say hello" autocomplete="off" aria-label="Server command" />
            <button type="submit" class="btn btn-primary btn-sm">Send</button>
          </form>
"""

_SECTION_CONSOLE_PREVIEW = f"""
        <section id="console" class="card">
          <div class="card-header">
            <h2>Console</h2>
            <a class="link" href="/dashboard/console">Open full console {_icon("arrow")}</a>
          </div>
          <pre id="console-output" class="console console-short">Waiting for logs...</pre>
          {_COMMAND_FORM}
        </section>
"""

_SECTION_SETUP = """
        <section id="setup" class="card">
          <form id="setup-form">
            <div class="form-grid">
              <div class="field">
                <label for="loader-select">Loader</label>
                <select id="loader-select" name="loader">
                  <option value="vanilla">Vanilla</option>
                  <option value="paper">Paper (plugins)</option>
                  <option value="fabric">Fabric (mods)</option>
                  <option value="forge">Forge (mods)</option>
                  <option value="neoforge">NeoForge (mods)</option>
                </select>
              </div>
              <div class="field">
                <label for="version-select">Minecraft version</label>
                <select id="version-select" name="version"></select>
              </div>
              <div class="field">
                <label for="ram-input">Memory (MB)</label>
                <input id="ram-input" name="ram_mb" type="number" min="512" step="256" value="2048" />
                <p class="hint">2048–4096 MB suits most small servers.</p>
              </div>
              <div class="field">
                <label for="port-input">Port</label>
                <input id="port-input" name="server_port" type="number" min="1024" max="65535" value="25565" />
                <p class="hint">Default Minecraft port is 25565.</p>
              </div>
            </div>
            <div class="form-actions">
              <p class="hint">Creating downloads Java and the server files if they are missing. This can take a few minutes.</p>
              <button type="submit" class="btn btn-ghost">Save</button>
              <button id="create-button" type="button" class="btn btn-primary">Create server</button>
            </div>
          </form>
        </section>
"""


def _switch(element_id: str, label: str, hint: str, checked: bool) -> str:
    return (
        f'<label class="switch-row" for="{element_id}">'
        f'<span><span class="switch-label">{label}</span><span class="hint">{hint}</span></span>'
        f'<input id="{element_id}" type="checkbox" class="switch"{" checked" if checked else ""} />'
        "</label>"
    )


_SECTION_ADVANCED = f"""
        <section id="advanced" class="settings-layout">
          <div class="card">
            <div class="card-header"><h2>World</h2></div>
            <div class="form-grid">
              <div class="field field-wide">
                <label for="motd-input">Server name (MOTD)</label>
                <input id="motd-input" value="EZ Minecraft Server" />
              </div>
              <div class="field">
                <label for="gamemode-input">Game mode</label>
                <select id="gamemode-input">
                  <option value="survival">Survival</option>
                  <option value="creative">Creative</option>
                  <option value="adventure">Adventure</option>
                  <option value="spectator">Spectator</option>
                </select>
              </div>
              <div class="field">
                <label for="difficulty-input">Difficulty</label>
                <select id="difficulty-input">
                  <option value="peaceful">Peaceful</option>
                  <option value="easy">Easy</option>
                  <option value="normal">Normal</option>
                  <option value="hard">Hard</option>
                </select>
              </div>
              <div class="field">
                <label for="leveltype-input">World type</label>
                <select id="leveltype-input">
                  <option value="normal">Normal</option>
                  <option value="flat">Flat</option>
                  <option value="largebiomes">Large biomes</option>
                  <option value="amplified">Amplified</option>
                </select>
              </div>
              <div class="field">
                <label for="seed-input">Seed</label>
                <input id="seed-input" value="" placeholder="Random" />
              </div>
              <div class="field">
                <label for="maxplayers-input">Max players</label>
                <input id="maxplayers-input" type="number" min="1" max="1000" value="10" />
              </div>
              <div class="field">
                <label for="viewdist-input">View distance (chunks)</label>
                <input id="viewdist-input" type="number" min="3" max="32" value="10" />
              </div>
              <div class="field">
                <label for="simdist-input">Simulation distance (chunks)</label>
                <input id="simdist-input" type="number" min="3" max="32" value="10" />
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h2>Rules</h2></div>
            <div class="switch-list">
              {_switch("online-mode", "Online mode", "Verify accounts with Mojang. Turn off only for offline LAN play.", True)}
              {_switch("pvp-mode", "PvP", "Players can damage each other.", True)}
              {_switch("spawn-monsters", "Spawn monsters", "Hostile mobs appear at night and in the dark.", True)}
              {_switch("nether-mode", "Nether", "Allow travel to the Nether.", True)}
              {_switch("allow-flight", "Allow flight", "Don't kick players who fly (needed by some mods).", False)}
              {_switch("whitelist-mode", "Whitelist", "Only whitelisted players can join.", False)}
              {_switch("command-blocks", "Command blocks", "Enable command blocks in the world.", True)}
            </div>
          </div>

          <div class="save-bar">
            <p class="hint">Changes apply the next time the server starts.</p>
            <button id="advanced-save-button" class="btn btn-primary" type="button">Save settings</button>
          </div>
        </section>
"""

_SECTION_ALWAYS_ON = f"""
        <section id="always-on" class="card">
          <div class="card-header">
            <div>
              <h2>Always-on</h2>
              <p class="card-sub">For a 24/7 server: what happens when the dashboard starts or the server crashes.</p>
            </div>
          </div>
          <div class="switch-list">
            {_switch("autostart-server", "Start Minecraft with the dashboard", "Starts the server automatically after a reboot or dashboard restart.", False)}
            {_switch("auto-restart", "Restart after a crash", "Waits 10 seconds, then restarts. Pauses after 3 crashes in 10 minutes.", True)}
            {_switch("autostart-playit", "Start the PlayIt tunnel with the dashboard", "Friends can reconnect after a reboot without you doing anything.", False)}
          </div>
          <div class="form-actions">
            <p class="hint">To start the dashboard itself on boot, see "Run 24/7" in the README.</p>
            <button id="always-on-save-button" class="btn btn-primary" type="button">Save</button>
          </div>
        </section>
"""

_SECTION_MODS = f"""
        <section id="mods" class="two-col">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Find on Modrinth</h2>
                <p class="card-sub">Results match your loader and Minecraft version.</p>
              </div>
            </div>
            <form id="mod-search-form" class="search-form">
              {_icon("search")}
              <input id="mod-search-query" placeholder="Search mods or plugins" aria-label="Search Modrinth" />
              <button id="mod-search-button" class="btn btn-primary btn-sm" type="submit">Search</button>
            </form>
            <div id="mod-search-results" class="item-list" aria-live="polite">
              <p class="empty">Search Modrinth to see results here.</p>
            </div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Installed</h2>
                <p class="card-sub">Jars in the server's mods or plugins folder.</p>
              </div>
              <div class="card-tools">
                <button id="mod-refresh-button" class="icon-btn" type="button" title="Refresh" aria-label="Refresh installed list">{_icon("restart")}</button>
                <label class="btn btn-ghost btn-sm" for="mod-upload-input">{_icon("upload")} Upload .jar</label>
                <input id="mod-upload-input" class="visually-hidden" type="file" multiple accept=".jar,application/java-archive" />
              </div>
            </div>
            <div id="mod-installed-list" class="item-list"></div>
          </div>
        </section>
"""

_SECTION_REMOTE = f"""
        <section id="remote" class="two-col">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>PlayIt tunnel</h2>
                <p class="card-sub">Free tunnel through playit.gg — no router port forwarding needed.</p>
              </div>
              <span id="playit-status-panel" class="badge">PlayIt not configured.</span>
            </div>
            <div class="address-row address-row-lg">
              <span class="address-kind">Address</span>
              <code data-field="playit_address">Not available yet</code>{_copy_button("playit_address", "PlayIt address")}
            </div>
            <div id="playit-claim" class="callout callout-info" hidden>
              <strong>Approve this launcher on playit.gg</strong>
              <p>Open the link on any device, sign in to playit.gg and click <em>Allow</em>. This page updates by itself.</p>
              <div class="address-row">
                <a id="playit-claim-link" class="link" href="#" target="_blank" rel="noopener noreferrer"
                   data-field="playit_claim_url">—</a>{_copy_button("playit_claim_url", "approval link")}
              </div>
            </div>
            <p id="playit-error" class="callout callout-error" hidden></p>
            <ol class="steps">
              <li><strong>Configure</strong> once: approve the link that appears here.</li>
              <li><strong>Start</strong> the tunnel whenever the server is running.</li>
              <li>Share the address above with your friends.</li>
            </ol>
            <div class="form-actions">
              <button id="playit-config-button" class="btn btn-ghost" type="button">Configure</button>
              <button id="playit-stop-button" class="btn btn-ghost" type="button">Stop tunnel</button>
              <button id="playit-start-button" class="btn btn-primary" type="button">Start tunnel</button>
            </div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Dashboard over the internet</h2>
                <p class="card-sub">A private https:// link through Cloudflare, free and without an account.</p>
              </div>
              <span id="remote-dashboard-badge" class="badge">Off</span>
            </div>
            <label class="switch-row" for="remote-dashboard-toggle">
              <span><span class="switch-label">Share this dashboard</span>
                <span class="hint">Stays on after reboots while this is enabled.</span></span>
              <input id="remote-dashboard-toggle" type="checkbox" class="switch" />
            </label>
            <div class="address-row address-row-lg">
              <span class="address-kind">Link</span>
              <a id="remote-dashboard-link" class="link" href="#" target="_blank" rel="noopener noreferrer"
                 data-field="remote_dashboard_url">Off</a>{_copy_button("remote_dashboard_url", "dashboard link")}
            </div>
            <p id="remote-dashboard-error" class="callout callout-error" hidden></p>
            <div class="callout callout-info">
              <strong>Encrypted, with limits.</strong> The link changes every time the tunnel restarts, so
              copy it again after a reboot, and a new link can take up to a minute to open everywhere.
              Anyone with the link can try to sign in, and there is no lockout for wrong passwords yet:
              use a long, unique password.
            </div>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Direct connection</h2>
                <p class="card-sub">Same Wi-Fi, or internet with the port forwarded on your router.</p>
              </div>
            </div>
            <div class="address-row address-row-lg"><span class="address-kind">LAN</span>
              <code data-field="lan_join">—</code>{_copy_button("lan_join", "LAN address")}</div>
            <div class="address-row address-row-lg"><span class="address-kind">Internet</span>
              <code data-field="public_join">—</code>{_copy_button("public_join", "internet address")}</div>
          </div>
        </section>
"""

_SECTION_BACKUPS = f"""
        <section id="backups" class="card">
          <div class="card-header">
            <div>
              <h2>World backups</h2>
              <p class="card-sub">Restoring moves the current world aside first, so nothing is lost. Stop the server before restoring.</p>
            </div>
            <button id="backup-button" class="btn btn-primary btn-sm" type="button">{_icon("archive")} Backup world</button>
          </div>
          <div id="backup-list" class="item-list"></div>
        </section>
"""

_SECTION_CONSOLE_FULL = f"""
        <section id="console" class="card console-card">
          <div class="card-header">
            <h2>Server output</h2>
            <label class="inline-check"><input id="console-autoscroll" type="checkbox" checked /> Auto-scroll</label>
          </div>
          <pre id="console-output" class="console console-tall">Waiting for logs...</pre>
          {_COMMAND_FORM}
        </section>

        <section id="admin" class="two-col">
          <div class="card">
            <div class="card-header"><h2>Players</h2></div>
            <div class="player-form">
              <input id="admin-player" placeholder="Player name" aria-label="Player name" />
              <div class="btn-row">
                <button id="admin-op" class="btn btn-ghost btn-sm" type="button">Make operator</button>
                <button id="admin-deop" class="btn btn-ghost btn-sm" type="button">Remove operator</button>
                <button id="admin-kick" class="btn btn-ghost btn-sm" type="button">Kick</button>
                <button id="admin-ban" class="btn btn-danger btn-sm" type="button">Ban</button>
              </div>
            </div>
          </div>
          <div class="card">
            <div class="card-header"><h2>World</h2></div>
            <div class="btn-row">
              <button id="admin-day" class="btn btn-ghost btn-sm" type="button">Set day</button>
              <button id="admin-night" class="btn btn-ghost btn-sm" type="button">Set night</button>
              <button id="admin-clear" class="btn btn-ghost btn-sm" type="button">Clear weather</button>
              <button id="admin-rain" class="btn btn-ghost btn-sm" type="button">Rain</button>
              <button id="admin-save" class="btn btn-ghost btn-sm" type="button">Save world</button>
            </div>
          </div>
        </section>
"""

_DASHBOARD_PAGES: dict[str, tuple[str, ...]] = {
    "overview": (_SECTION_OVERVIEW_TILES, _SECTION_METRICS, _SECTION_CONSOLE_PREVIEW),
    "setup": (_SECTION_SETUP, _SECTION_ALWAYS_ON),
    "advanced": (_SECTION_ADVANCED,),
    "mods": (_SECTION_MODS,),
    "remote": (_SECTION_REMOTE,),
    "backups": (_SECTION_BACKUPS,),
    "console": (_SECTION_CONSOLE_FULL,),
}

_TOPBAR_CONTROLS = f"""
          <div class="topbar-actions">
            <span id="server-state" class="state-pill" data-state="unknown" role="status">
              <span class="state-dot" aria-hidden="true"></span><span id="server-state-label">Loading…</span>
            </span>
            <div class="btn-group" role="group" aria-label="Server controls">
              <button id="start-button" class="btn btn-primary btn-sm" type="button" title="Start server" disabled>{_icon("play")} Start</button>
              <button id="restart-button" class="btn btn-ghost btn-sm" type="button" title="Restart server" disabled>{_icon("restart")} Restart</button>
              <button id="stop-button" class="btn btn-ghost btn-sm" type="button" title="Stop server" disabled>{_icon("stop")} Stop</button>
            </div>
          </div>
"""


def _render_dashboard_page(section: str) -> HTMLResponse:
    normalized = section.strip("/").lower()
    if normalized not in _DASHBOARD_PAGES:
        raise HTTPException(status_code=404, detail=f"Dashboard section '{section}' not found.")

    nav_links = []
    for key, label, _title, _sub in _DASHBOARD_NAVIGATION:
        current = ' active" aria-current="page' if key == normalized else ""
        nav_links.append(f'<a href="/dashboard/{key}" class="nav-link{current}">{_icon(key)}<span>{label}</span></a>')
    nav_html = "".join(nav_links)
    _key, _label, title, subtitle = next(item for item in _DASHBOARD_NAVIGATION if item[0] == normalized)
    body_html = "".join(_DASHBOARD_PAGES[normalized])

    return HTMLResponse(
        f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title} · EZ Minecraft Server</title>
    <link rel="stylesheet" href="/dashboard.css" />
  </head>
  <body data-page="{normalized}">
    <div class="app-shell">
      <aside class="sidebar">
        <a class="brand" href="/dashboard/overview">
          <span class="brand-logo" aria-hidden="true"></span>
          <span>
            <span class="brand-title">EZ Minecraft</span>
            <span class="brand-sub">Server Dashboard</span>
          </span>
        </a>

        <nav class="nav" aria-label="Dashboard sections">{nav_html}</nav>

        <div class="sidebar-footer">
          <span class="session" id="session-status">Signed in</span>
          <button id="logout-button" class="icon-btn" type="button" title="Sign out" aria-label="Sign out">{_icon("logout")}</button>
        </div>
      </aside>

      <div class="main">
        <header class="topbar">
          <div>
            <h1 class="page-title">{title}</h1>
            <p class="page-sub">{subtitle}</p>
          </div>
          {_TOPBAR_CONTROLS}
        </header>
        <main class="content">
          {body_html}
        </main>
      </div>
    </div>
    <div id="action-status" class="toast" role="status" aria-live="polite" hidden></div>
    <script src="/dashboard.js"></script>
  </body>
</html>
        """
    )


_APP_START_TIME = time.time()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # autostart/shutdown are set at the end of create_app.
    app.state.autostart()
    yield
    await asyncio.to_thread(app.state.shutdown)


def create_app() -> FastAPI:
    validate_auth_settings()
    app = FastAPI(title="EZ Minecraft Server Launcher", version="0.1.0", lifespan=_lifespan)
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

    def handle_crash(code: int) -> None:
        """Schedule an automatic restart, unless the server keeps crashing."""
        now = time.monotonic()
        runtime.crash_times = [t for t in runtime.crash_times if now - t < CRASH_WINDOW_SEC]
        runtime.crash_times.append(now)
        attempt = len(runtime.crash_times)
        if attempt > CRASH_RESTART_LIMIT:
            runtime.state = "crashed"
            append_console_line(
                f"[server] crashed {attempt} times in {CRASH_WINDOW_SEC // 60} minutes; automatic restart paused. "
                "Check the log above, fix the problem and press Start."
            )
            return
        append_console_line(
            f"[server] crashed (exit code {code}); restarting in {CRASH_RESTART_DELAY_SEC} s "
            f"(attempt {attempt} of {CRASH_RESTART_LIMIT})"
        )
        timer = threading.Timer(CRASH_RESTART_DELAY_SEC, restart_after_crash)
        timer.daemon = True
        timer.start()

    def restart_after_crash() -> None:
        if runtime.stop_requested or (runtime.process is not None and runtime.process.is_running()):
            return  # the user stopped or started the server in the meantime
        try:
            launch_server()
        except Exception as exc:
            runtime.state = "stopped"
            runtime.operation = "idle"
            append_console_line(f"[server] automatic restart failed: {exc}")

    def launch_server(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Install if needed and start the Minecraft server (used by the API, crash restart and autostart)."""
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
        runtime.stop_requested = False

        process: Any = None

        def on_exit(code: int) -> None:
            append_console_line(f"[server] exited with code {code}")
            if runtime.process is not process:
                return  # a newer process already replaced this one (e.g. after a restart)
            runtime.state = "stopped"
            runtime.operation = "idle"
            runtime.process = None
            runtime.clear_players()
            if code != 0 and not runtime.stop_requested and _effective_config().get("auto_restart"):
                handle_crash(code)

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

    @app.post("/api/server/start")
    def start_server(payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}
        runtime.crash_times = []  # a manual start re-arms automatic crash restarts
        return launch_server(payload)

    @app.post("/api/server/stop")
    def stop_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        runtime.stop_requested = True  # also cancels a pending crash restart
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
        if command.lstrip("/").lower() == "stop":
            runtime.stop_requested = True  # a console "stop" is deliberate, not a crash
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

    def playit_log(line: str) -> None:
        append_console_line(f"[playit] {line}")

    def playit_info(message: str = "") -> dict[str, Any]:
        cfg = playit.load_config()
        return {
            "configured": bool(cfg.get("secret")),
            "state": _playit_state(runtime, cfg),
            "address": cfg.get("address") or "",
            "secret_present": bool(cfg.get("secret")),
            "setup": runtime.playit_setup,
            "claim_url": runtime.playit_claim_url or "",
            "error": runtime.playit_error,
            "message": message,
        }

    def start_agent_and_tunnels(secret: str) -> None:
        """Start the agent if needed, ensure the Minecraft tunnel and save its address."""
        if not (runtime.playit_agent and runtime.playit_agent.is_running()):
            playit.ensure_agent(log=playit_log)
            runtime.playit_agent = playit.PlayitAgent(
                on_output=append_console_line,
                on_exit=lambda code: _playit_on_exit(app.state.console_manager, runtime, code),
            )
            runtime.playit_agent.start(secret)
        config = _effective_config()
        address = playit.ensure_tunnel(secret, int(config.get("server_port", 25565)), playit_log)
        playit.save_config(secret, address)
        runtime.playit_state = "running"
        runtime.playit_address = address

    def claim_and_connect() -> None:
        """Background PlayIt setup: wait for the user's approval, then bring the tunnels up."""
        try:
            secret = playit.claim_interactive(
                playit_log, lambda: runtime.playit_cancel, on_url=lambda url: setattr(runtime, "playit_claim_url", url)
            )
            runtime.playit_setup = "connecting"
            runtime.playit_claim_url = None
            start_agent_and_tunnels(secret)
            runtime.playit_setup = "idle"
            playit_log("Setup complete; tunnel running.")
        except Exception as exc:
            runtime.playit_setup = "error"
            runtime.playit_claim_url = None
            runtime.playit_error = str(exc)
            playit_log(f"Setup failed: {exc}")

    @app.get("/api/playit/status")
    def playit_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        return playit_info()

    @app.post("/api/playit/configure")
    def configure_playit(payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.playit_setup in {"awaiting-approval", "connecting"}:
            return playit_info("PlayIt setup already in progress.")
        if (payload or {}).get("reset"):
            # Link a different account: stop the agent and forget the saved secret first.
            if runtime.playit_agent and runtime.playit_agent.is_running():
                runtime.playit_agent.stop()
            runtime.playit_agent = None
            playit.forget_config()
            playit_log("Saved PlayIt account removed.")
        cfg = playit.load_config()
        if cfg.get("secret"):
            runtime.playit_address = cfg.get("address") or runtime.playit_address
            return playit_info("PlayIt already configured.")

        runtime.playit_setup = "awaiting-approval"
        runtime.playit_claim_url = None
        runtime.playit_error = ""
        runtime.playit_cancel = False
        threading.Thread(target=claim_and_connect, daemon=True).start()
        deadline = time.monotonic() + 5
        while runtime.playit_claim_url is None and runtime.playit_setup == "awaiting-approval" and time.monotonic() < deadline:
            time.sleep(0.05)
        return playit_info("Open the approval link and click Allow.")

    @app.post("/api/playit/start")
    def start_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        if not cfg.get("secret"):
            return playit_info("PlayIt is not configured yet. Click Configure first.")
        if runtime.playit_agent and runtime.playit_agent.is_running():
            return playit_info("PlayIt tunnel is already running.")
        try:
            start_agent_and_tunnels(cfg["secret"])
        except Exception as exc:
            playit_log(f"Start failed: {exc}")
            raise HTTPException(status_code=502, detail=f"PlayIt start failed: {exc}") from exc
        return playit_info("PlayIt tunnel started.")

    @app.post("/api/playit/stop")
    def stop_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.playit_setup in {"awaiting-approval", "connecting"}:
            runtime.playit_cancel = True
        if runtime.playit_agent and runtime.playit_agent.is_running():
            runtime.playit_agent.stop()
        runtime.playit_agent = None
        cfg = playit.load_config()
        runtime.playit_state = "stopped"
        runtime.playit_address = cfg.get("address") or runtime.playit_address
        return playit_info("PlayIt tunnel stopped.")

    @app.get("/api/playit/config")
    def playit_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        # Never expose the secret key over HTTP; it stays in playit_config.json on the host.
        cfg = playit.load_config()
        return {"address": cfg.get("address") or "", "secret_present": bool(cfg.get("secret"))}

    # ---------- Remote dashboard (Cloudflare quick tunnel) ----------
    def web_log(line: str) -> None:
        append_console_line(f"[cloudflare] {line}")

    def remote_info(message: str = "") -> dict[str, Any]:
        return {
            "enabled": bool(_effective_config().get("remote_dashboard")),
            "state": runtime.web_tunnel_state,
            "url": runtime.web_tunnel_url or "",
            "error": runtime.web_tunnel_error,
            "message": message,
        }

    def start_web_tunnel() -> None:
        if runtime.web_tunnel is not None and runtime.web_tunnel.is_running():
            return
        runtime.web_tunnel_wanted = True
        runtime.web_tunnel_state = "starting"
        runtime.web_tunnel_error = ""
        runtime.web_tunnel_url = None
        binary = cloudflared.ensure_binary(log=web_log)
        tunnel: Any = None

        def on_url(url: str) -> None:
            runtime.web_tunnel_url = url
            runtime.web_tunnel_state = "running"
            web_log(f"Dashboard reachable at {url}")

        def on_output(line: str) -> None:
            if " ERR " in line or " WRN " in line:  # cloudflared is chatty; keep problems only
                web_log(line)

        def on_exit(code: int) -> None:
            if runtime.web_tunnel is not tunnel:
                return
            runtime.web_tunnel = None
            runtime.web_tunnel_url = None
            if not runtime.web_tunnel_wanted:
                runtime.web_tunnel_state = "off"
                return
            runtime.web_tunnel_state = "error"
            runtime.web_tunnel_error = f"cloudflared exited (code {code}); retrying in {WEB_TUNNEL_RETRY_SEC} s"
            web_log(runtime.web_tunnel_error)
            timer = threading.Timer(WEB_TUNNEL_RETRY_SEC, retry_web_tunnel)
            timer.daemon = True
            timer.start()

        tunnel = cloudflared.QuickTunnel(on_url=on_url, on_output=on_output, on_exit=on_exit)
        runtime.web_tunnel = tunnel
        tunnel.start(binary, WEB_PORT)

    def start_web_tunnel_safely() -> None:
        try:
            start_web_tunnel()
        except Exception as exc:
            runtime.web_tunnel_state = "error"
            runtime.web_tunnel_error = str(exc)
            web_log(f"Start failed: {exc}")

    def retry_web_tunnel() -> None:
        if runtime.web_tunnel_wanted and _effective_config().get("remote_dashboard"):
            start_web_tunnel_safely()

    def stop_web_tunnel() -> None:
        runtime.web_tunnel_wanted = False
        tunnel = runtime.web_tunnel
        runtime.web_tunnel = None
        runtime.web_tunnel_url = None
        runtime.web_tunnel_state = "off"
        runtime.web_tunnel_error = ""
        if tunnel is not None and tunnel.is_running():
            tunnel.stop()

    @app.get("/api/remote-dashboard")
    def remote_dashboard_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        return remote_info()

    @app.post("/api/remote-dashboard")
    def remote_dashboard_toggle(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        enabled = bool(payload.get("enabled"))
        config = _effective_config()
        config["remote_dashboard"] = enabled
        config_store.save(config)
        if not enabled:
            stop_web_tunnel()
            return remote_info("The dashboard is no longer reachable from the internet.")
        runtime.web_tunnel_state = "starting"
        runtime.web_tunnel_error = ""
        threading.Thread(target=start_web_tunnel_safely, name="cloudflared", daemon=True).start()
        return remote_info("Starting the Cloudflare tunnel; the link appears in a few seconds.")

    def run_in_background(name: str, action: Any) -> None:
        def worker() -> None:
            try:
                action()
            except Exception as exc:
                append_console_line(f"[autostart] {name} failed: {exc}")

        threading.Thread(target=worker, name=f"autostart-{name}", daemon=True).start()

    def autostart() -> None:
        """Bring up what the Always-on settings ask for. EZ_NO_AUTOSTART=1 skips it (debugging)."""
        if os.getenv("EZ_NO_AUTOSTART") == "1":
            return
        config = _effective_config()
        secret = playit.load_config().get("secret")
        if config.get("autostart_playit") and secret:
            append_console_line("[autostart] starting the PlayIt tunnel")
            run_in_background("PlayIt tunnel", lambda: start_agent_and_tunnels(secret))
        if config.get("autostart_server"):
            append_console_line("[autostart] starting the Minecraft server")
            run_in_background("Minecraft server", launch_server)
        if config.get("remote_dashboard"):
            append_console_line("[autostart] starting the remote dashboard link")
            run_in_background("remote dashboard", start_web_tunnel_safely)

    def shutdown() -> None:
        """Stop Minecraft and the PlayIt agent with the dashboard, so no orphan keeps the port or world lock."""
        runtime.stop_requested = True
        process = runtime.process
        if process is not None and process.is_running():
            append_console_line("[server] dashboard shutting down; stopping the server")
            process.stop()
            if not _wait_for_exit(process, SHUTDOWN_STOP_TIMEOUT_SEC):
                process.kill()
        if runtime.playit_agent is not None and runtime.playit_agent.is_running():
            runtime.playit_agent.stop()
        stop_web_tunnel()

    app.state.autostart = autostart
    app.state.shutdown = shutdown

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
