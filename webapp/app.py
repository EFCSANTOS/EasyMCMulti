from __future__ import annotations

import asyncio
import glob
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import os
import platform
import threading
import time
import zipfile
from typing import Any

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - installed in project venv for runtime metrics
    psutil = None

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.background import BackgroundTask

from core import (
    cloudflared, config_store, downloaders, fsutil, java_manager, modrinth, netinfo, notifier, paths, playit, updater,
    worlds,
)
from core.server_process import ServerProcess, accept_eula, update_properties
from webapp.auth import (
    SESSION_COOKIE, SESSION_TTL_SEC, LoginLimiter, SessionStore, authenticate, check_login, is_https, password_is_weak,
    validate_auth_settings,
)
from webapp.state import RuntimeState
from webapp.websocket_manager import WebSocketManager

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
RESTART_STOP_TIMEOUT_SEC = 60
SHUTDOWN_STOP_TIMEOUT_SEC = 45
# Crash auto-restart: wait CRASH_RESTART_DELAY_SEC, at most CRASH_RESTART_LIMIT times per CRASH_WINDOW_SEC.
CRASH_RESTART_DELAY_SEC = 10
CRASH_RESTART_LIMIT = 3
CRASH_WINDOW_SEC = 600
# Background lifecycle callers (crash restart, autostart) wait at most this long for a running
# install/start/restart to finish; routes never wait (409 "Busy").
LIFECYCLE_WAIT_SEC = 600
# Port uvicorn listens on; run.bat / run.sh pass the same value. Used for the remote dashboard tunnel.
WEB_PORT = int(os.getenv("EZ_WEBAPP_PORT", "8000"))
WEB_TUNNEL_RETRY_SEC = 15
# Alerts: the same alert is emailed at most once per ALERT_REPEAT_SEC; heartbeat every HEARTBEAT_INTERVAL_SEC.
ALERT_REPEAT_SEC = 600
PLAYIT_RETRY_SEC = 30
# More than PLAYIT_CRASH_LIMIT agent exits within PLAYIT_CRASH_WINDOW_SEC pause automatic restarts.
PLAYIT_CRASH_LIMIT = 3
PLAYIT_CRASH_WINDOW_SEC = 600
PLAYIT_KEY_REJECTED = ("playit.gg rejected the saved secret key (InvalidAgentKey). This happens when the agent was "
                       "deleted on playit.gg or the key was revoked. Click \"Change account\" to link this launcher again.")
MODRINTH_PAGE_SIZE = 20
# Self-update: first check shortly after start, then every UPDATE_CHECK_INTERVAL_SEC.
UPDATE_FIRST_CHECK_SEC = 120
UPDATE_CHECK_INTERVAL_SEC = 6 * 3600
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
# Changes whenever the CSS/JS change (e.g. after an update) so browsers don't keep stale copies.
ASSET_VERSION = str(int(max(
    os.path.getmtime(os.path.join(_STATIC_DIR, "css", "dashboard.css")),
    os.path.getmtime(os.path.join(_STATIC_DIR, "js", "dashboard.js")),
)))
HEARTBEAT_INTERVAL_SEC = 60
FALLBACK_VERSIONS = {
    "vanilla": ["1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.20.4"],
    "paper": ["1.21.7", "1.21.6", "1.21.4", "1.21.1", "1.20.4"],
    "fabric": ["1.21.7", "1.21.6", "1.21.4", "1.20.4"],
    "forge": ["1.20.1", "1.19.2", "1.16.5"],
    "neoforge": ["1.21.7", "1.21.6", "1.21.1", "1.20.1"],
}

def require_auth(request: Request) -> str:
    """Session cookie (browser) or HTTP Basic (scripts); both share the failed-login lockout."""
    state = request.app.state
    return authenticate(request, state.sessions, state.login_limiter, state.on_lockout)


def _server_properties_from_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "server-port": int(config.get("server_port", 25565)),
        "online-mode": bool(config.get("online_mode", True)),
        "max-players": int(config.get("max_players", 10)),
        "motd": str(config.get("motd", "EZ Minecraft Server")),
        "level-seed": str(config.get("level_seed", "")),
        "level-name": _active_world(config),
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
        # Folder of the world Minecraft loads (Worlds page); worlds are folders in the server dir.
        "level_name": "world",
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
        # Self-update from the git repository (Server setup > Updates).
        "auto_update_check": True,
        "auto_update_install": False,
        # Cloudflare link to the dashboard (Remote access page); on by default (owner decision
        # 2026-10-22) so the link comes up by itself after every boot. Logins lock out after
        # repeated wrong passwords (webapp/auth.py).
        "remote_dashboard": True,
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
    """True if a backup zip entry stays inside a world folder (no absolute paths, drive letters or '..')."""
    normalized = member.replace("\\", "/")
    parts = normalized.split("/")
    return (
        len(parts) > 1
        and worlds.valid_folder(parts[0])
        and not normalized.startswith("/")
        and ".." not in parts
    )


def _active_world(config: dict[str, Any]) -> str:
    folder = str(config.get("level_name") or "world")
    return folder if worlds.valid_folder(folder) else "world"


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
    "worlds": '<polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/><line x1="9" y1="3" x2="9" y2="18"/>'
              '<line x1="15" y1="6" x2="15" y2="21"/>',
    "close": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
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
    ("worlds", "Worlds", "Worlds", "Upload, switch between and manage your worlds."),
    ("mods", "Mods", "Mods & plugins", "Find mods on Modrinth or upload your own .jar files."),
    ("remote", "Remote access", "Remote access", "Let friends outside your network join."),
    ("backups", "Backups", "Backups", "Snapshot the world and roll back when needed."),
    ("console", "Console", "Console", "Live server output, commands and admin shortcuts."),
)

# Shown on every page while an install runs (installs continue in the background).
_INSTALL_BANNER = """
        <div id="install-banner" class="card install-banner" hidden>
          <div class="install-head">
            <strong>Installing server</strong>
            <span data-field="install_message" class="muted"></span>
          </div>
          <div class="progress"><div id="install-progress-bar" class="progress-bar"></div></div>
        </div>
"""

_SECTION_OVERVIEW_TILES = f"""
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

_SECTION_ALERTS = f"""
        <section id="alerts" class="card">
          <div class="card-header">
            <div>
              <h2>Alerts</h2>
              <p class="card-sub">Get an email when the server crashes, the dashboard link changes or a tunnel has a problem.</p>
            </div>
          </div>
          <form id="alerts-form" autocomplete="off">
            <div class="switch-list">
              {_switch("alerts-email-enabled", "Email me", "Uses your Gmail (or any SMTP) account to send the alerts.", False)}
            </div>
            <div class="form-grid alerts-grid">
              <div class="field">
                <label for="alerts-email-to">Send alerts to</label>
                <input id="alerts-email-to" type="email" placeholder="you@example.com" />
              </div>
              <div class="field">
                <label for="alerts-smtp-user">Send from (Gmail address)</label>
                <input id="alerts-smtp-user" type="email" placeholder="yourname@gmail.com" />
              </div>
              <div class="field">
                <label for="alerts-smtp-password">App password</label>
                <input id="alerts-smtp-password" type="password" autocomplete="new-password" placeholder="16-character app password" />
              </div>
            </div>
            <p class="hint">Gmail: create an app password at myaccount.google.com/apppasswords (needs 2-step verification).
              It is not your normal password and only works for sending mail.</p>
            <details class="advanced-smtp">
              <summary>Other email provider</summary>
              <div class="form-grid">
                <div class="field">
                  <label for="alerts-smtp-host">SMTP server</label>
                  <input id="alerts-smtp-host" value="smtp.gmail.com" />
                </div>
                <div class="field">
                  <label for="alerts-smtp-port">SMTP port</label>
                  <input id="alerts-smtp-port" type="number" min="1" max="65535" value="587" />
                  <p class="hint">587 (STARTTLS) or 465 (SSL).</p>
                </div>
              </div>
            </details>
            <div class="switch-list alerts-events">
              {_switch("alerts-notify-server", "Server crashes and outages", "Crash, auto-restart paused, back online.", True)}
              {_switch("alerts-notify-link", "New dashboard link", "Emails the new https link after every restart or reboot.", True)}
              {_switch("alerts-notify-tunnels", "Tunnel problems", "PlayIt or the dashboard link stopped unexpectedly.", True)}
              {_switch("alerts-notify-updates", "Updates", "A new version is available, or an update failed.", True)}
              {_switch("alerts-notify-security", "Sign-in lockouts", "Someone entered a wrong dashboard password too many times.", True)}
            </div>
            <div class="field heartbeat-field">
              <label for="alerts-heartbeat-url">Heartbeat URL (optional, for when the whole phone goes down)</label>
              <input id="alerts-heartbeat-url" type="url" placeholder="https://hc-ping.com/your-check-id" />
              <p class="hint">Create a free check at healthchecks.io and paste its ping URL. The dashboard checks in every
                minute; if it stops (phone off, no internet, app killed), healthchecks.io emails you.</p>
            </div>
            <div class="form-actions">
              <p class="hint">Each alert is sent at most once every 10 minutes.</p>
              <button id="alerts-test-button" class="btn btn-ghost" type="button">Send test</button>
              <button id="alerts-save-button" class="btn btn-primary" type="submit">Save</button>
            </div>
          </form>
        </section>
"""

_SECTION_UPDATES = """
        <section id="updates" class="card">
          <div class="card-header">
            <div>
              <h2>Updates</h2>
              <p class="card-sub">Get the latest version from the git repository this app was installed from.</p>
            </div>
            <span id="update-badge" class="badge">Checking…</span>
          </div>
          <div class="address-row address-row-lg">
            <span class="address-kind">Installed</span>
            <code data-field="update_current">—</code>
          </div>
          <p id="update-summary" class="hint"></p>
          <ul id="update-commits" class="commit-list" hidden></ul>
          <p id="update-error" class="callout callout-error" hidden></p>
          <div class="switch-list">
            <label class="switch-row" for="update-auto-check">
              <span><span class="switch-label">Check for updates automatically</span>
                <span class="hint">Every 6 hours; emails you when an update is available (see Alerts).</span></span>
              <input id="update-auto-check" type="checkbox" class="switch" checked />
            </label>
            <label class="switch-row" for="update-auto-install">
              <span><span class="switch-label">Install updates automatically</span>
                <span class="hint">Only when no one is online. Minecraft restarts for about a minute.</span></span>
              <input id="update-auto-install" type="checkbox" class="switch" />
            </label>
          </div>
          <div class="form-actions">
            <p class="hint">Updating restarts the dashboard; Minecraft and PlayIt come back if they were running.</p>
            <button id="update-check-button" class="btn btn-ghost" type="button">Check now</button>
            <button id="update-apply-button" class="btn btn-primary" type="button" disabled>Update &amp; restart</button>
          </div>
        </section>
"""

_SECTION_MODS = f"""
        <section id="mods" class="stack">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Browse Modrinth</h2>
                <p id="mod-context" class="card-sub">Mods and plugins that work on your server.</p>
              </div>
            </div>
            <form id="mod-search-form" class="mod-toolbar">
              <div class="search-form">
                {_icon("search")}
                <input id="mod-search-query" type="search" placeholder="Search, or just browse below" aria-label="Search Modrinth" />
                <button id="mod-search-button" class="btn btn-primary btn-sm" type="submit">Search</button>
              </div>
              <select id="mod-category" aria-label="Category"><option value="">All categories</option></select>
              <select id="mod-sort" aria-label="Sort by">
                <option value="downloads">Most downloaded</option>
                <option value="relevance">Best match</option>
                <option value="follows">Most followed</option>
                <option value="updated">Recently updated</option>
                <option value="newest">Newest</option>
              </select>
            </form>
            <p id="mod-result-count" class="hint"></p>
            <div id="mod-search-results" class="mod-grid" aria-live="polite"></div>
            <div class="load-more-row">
              <button id="mod-load-more" class="btn btn-ghost" type="button" hidden>Load more</button>
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
            <p class="hint">Restart the server after installing or removing mods. Removing needs the server stopped.</p>
          </div>
        </section>

        <dialog id="mod-details" class="dialog" aria-labelledby="mod-details-title">
          <button id="mod-details-close" class="icon-btn dialog-close" type="button" aria-label="Close">{_icon("close")}</button>
          <div id="mod-details-body"></div>
        </dialog>
"""

_SECTION_WORLDS = f"""
        <section id="worlds" class="stack">
          <div class="card">
            <div class="card-header">
              <div>
                <h2>Your worlds</h2>
                <p class="card-sub">Every world stays on this device. Switching only changes which one the server loads.</p>
              </div>
              <div class="card-tools">
                <button id="world-refresh-button" class="icon-btn" type="button" title="Refresh" aria-label="Refresh worlds">{_icon("restart")}</button>
                <label class="btn btn-primary btn-sm" for="world-upload-input">{_icon("upload")} Upload world (.zip)</label>
                <input id="world-upload-input" class="visually-hidden" type="file" accept=".zip,application/zip" />
              </div>
            </div>
            <div id="world-list" class="item-list"></div>
            <p class="hint">Upload a zipped world folder: from singleplayer it's in <code>.minecraft/saves/&lt;world&gt;</code>.
              Through the internet link, uploads are limited to about 100 MB; use the LAN address for bigger worlds.</p>
          </div>
          <div class="card">
            <div class="card-header">
              <div>
                <h2>New world</h2>
                <p class="card-sub">Generated the first time the server starts on it.</p>
              </div>
            </div>
            <form id="world-create-form" class="form-grid">
              <div class="field">
                <label for="world-name">Name</label>
                <input id="world-name" maxlength="80" placeholder="e.g. Survival 2" required />
              </div>
              <div class="field">
                <label for="world-seed">Seed (optional)</label>
                <input id="world-seed" placeholder="Random" />
              </div>
              <div class="field field-action">
                <button class="btn btn-primary" type="submit">Create world</button>
              </div>
            </form>
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
            <details id="playit-json" class="advanced-smtp">
              <summary>Edit config (JSON)</summary>
              <div class="field">
                <label for="playit-json-input">playit_config.json</label>
                <textarea id="playit-json-input" rows="5" spellcheck="false" autocomplete="off"></textarea>
                <p class="hint">Paste a secret key from playit.gg into "secret", or leave it empty to keep the saved key
                  (it is never shown here). Saving cancels a pending approval.</p>
              </div>
              <div class="form-actions">
                <button id="playit-json-reload" class="btn btn-ghost" type="button">Reload</button>
                <button id="playit-json-save" class="btn btn-primary" type="button">Save config</button>
              </div>
            </details>
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
            <p id="remote-dashboard-weak-password" class="callout callout-error" hidden>
              <strong>Your dashboard password is short.</strong> Anyone with the link can try to guess it. Set
              EZ_WEBAPP_PASSWORD to 12 or more characters and restart the dashboard.
            </p>
            <div class="callout callout-info">
              <strong>Encrypted, with limits.</strong> The link changes every time the tunnel restarts, so
              copy it again after a reboot, and a new link can take up to a minute to open everywhere.
              Anyone with the link can try to sign in; after 5 wrong passwords that address is locked out
              for 15 minutes. Use a long, unique password.
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
    "setup": (_SECTION_SETUP, _SECTION_ALWAYS_ON, _SECTION_ALERTS, _SECTION_UPDATES),
    "advanced": (_SECTION_ADVANCED,),
    "worlds": (_SECTION_WORLDS,),
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
    <link rel="stylesheet" href="/dashboard.css?v={ASSET_VERSION}" />
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
          {_INSTALL_BANNER}
          {body_html}
        </main>
      </div>
    </div>
    <div id="action-status" class="toast" role="status" aria-live="polite" hidden></div>
    <script src="/dashboard.js?v={ASSET_VERSION}"></script>
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

    fsutil.set_logger(lambda message: append_console_line(f"[config] {message}"))

    # ---------- Alerts (email + heartbeat) ----------
    last_alert_at: dict[str, float] = {}

    def notify(category: str, subject: str, body: str, throttle: bool = True) -> None:
        """Email an alert in the background if enabled for `category` (server / link / tunnels).

        The same subject is sent at most once per ALERT_REPEAT_SEC so a flapping service can't spam.
        """
        config = notifier.load_config()
        if not notifier.email_ready(config) or not config.get(f"notify_{category}", True):
            return
        now = time.monotonic()
        if throttle and now - last_alert_at.get(subject, -ALERT_REPEAT_SEC) < ALERT_REPEAT_SEC:
            return
        last_alert_at[subject] = now
        full_body = (
            f"{body}\n\n"
            f"Host: {platform.node() or 'unknown'}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Dashboard: {runtime.web_tunnel_url or f'http://{netinfo.lan_ip()}:{WEB_PORT} (LAN)'}\n"
        )

        def send() -> None:
            try:
                notifier.send_email(config, f"[EZ Minecraft] {subject}", full_body)
                append_console_line(f"[notify] email sent: {subject}")
            except Exception as exc:
                append_console_line(f"[notify] email failed: {exc}")

        threading.Thread(target=send, name="notify", daemon=True).start()

    # ---------- Login sessions and failed-login lockout ----------
    app.state.sessions = SessionStore()
    app.state.login_limiter = LoginLimiter()

    def on_lockout(ip: str) -> None:
        append_console_line(f"[auth] Too many wrong passwords from {ip}; sign-in locked for 15 minutes.")
        notify("security", "Sign-in locked after wrong passwords",
               f"Someone at {ip} entered a wrong dashboard password too many times. Sign-in from there is "
               "locked for 15 minutes.\n\nIf this wasn't you, change EZ_WEBAPP_PASSWORD to a long, unique one.")

    app.state.on_lockout = on_lockout

    def heartbeat_loop() -> None:
        while not runtime.shutting_down:
            url = notifier.load_config().get("heartbeat_url")
            if url:
                notifier.ping_heartbeat(url)
            time.sleep(HEARTBEAT_INTERVAL_SEC)

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
            java_exe = java_manager.ensure_java(java_manager.required_java(version), log=install_log)
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

    @app.post("/api/login")
    def api_login(payload: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        check_login(request, app.state.login_limiter, username, password, on_lockout)
        token = app.state.sessions.create(username)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SEC, httponly=True, samesite="strict",
                            secure=is_https(request), path="/")
        return {"username": username}

    @app.post("/api/logout")
    def api_logout(request: Request, response: Response) -> dict[str, Any]:
        app.state.sessions.delete(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"message": "Signed out."}

    @app.get("/api/session")
    def api_session(username: str = Depends(require_auth)) -> dict[str, Any]:
        return {"username": username}

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
        try:
            config_store.save(config)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not save settings (disk full?): {exc}") from exc
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

    # ---------- Server lifecycle ----------
    # One operation that changes the server process (install, start, stop-and-wait, restart, world
    # switch, restore) runs at a time, under runtime.lifecycle. Routes never wait for it: they get 409
    # "Busy". Slow work runs on a thread so requests return at once (Cloudflare drops requests after
    # ~100 s); the dashboard follows progress through /api/status.
    def busy_error() -> HTTPException:
        label = runtime.operation if runtime.operation != "idle" else "another server operation"
        return HTTPException(status_code=409, detail=f"Busy: {label} in progress. Try again when it finishes.")

    def start_lifecycle_job(operation: str, job: Any, failure: str, state: str | None = None) -> None:
        """Run `job` (returns a completion message) on a thread while holding the lifecycle lock.

        Raises 409 when another lifecycle operation is running. A failure is recorded in
        runtime.operation_error and the console instead of being lost with the request.
        """
        if not runtime.lifecycle.acquire(blocking=False):
            raise busy_error()
        runtime.set_state(state or runtime.state, operation)
        runtime.operation_error = ""
        runtime.operation_message = ""

        def worker() -> None:
            try:
                runtime.operation_message = job()
            except Exception as exc:
                runtime.set_state("running" if server_running() else "stopped", "idle")
                runtime.operation_error = f"{failure}: {exc}"
                append_console_line(f"[server] {runtime.operation_error}")
            finally:
                runtime.lifecycle.release()

        threading.Thread(target=worker, name=f"lifecycle-{operation}", daemon=True).start()

    def accepted(response: Response, message: str, **extra: Any) -> dict[str, Any]:
        response.status_code = 202
        return {"state": runtime.state, "operation": runtime.operation, "message": message, **extra}

    def save_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
        config = _effective_config()
        if payload:
            config.update(payload)
            config_store.save(config)
        return config

    def install_server(config: dict[str, Any]) -> str:
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        runtime.loader = loader
        runtime.version = version
        runtime.set_state("ready", "creating")
        target_dir = ensure_server_installed(config)
        update_properties(target_dir, **_server_properties_from_config(config))
        runtime.set_state("stopped", "idle")
        return f"Server {loader} {version} is ready."

    @app.post("/api/server/create")
    def create_server(response: Response, payload: dict[str, Any] | None = None,
                      _: str = Depends(require_auth)) -> dict[str, Any]:
        if server_running():
            raise HTTPException(status_code=409, detail="Stop the server before creating it again.")
        config = save_payload(payload)
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        start_lifecycle_job("creating", lambda: install_server(config), "Server creation failed")
        return accepted(response, f"Creating {loader} {version}; downloads can take a few minutes.",
                        loader=loader, version=version)

    def handle_crash(code: int) -> None:
        """Schedule an automatic restart, unless the server keeps crashing."""
        now = time.monotonic()
        runtime.crash_times = [t for t in runtime.crash_times if now - t < CRASH_WINDOW_SEC]
        runtime.crash_times.append(now)
        attempt = len(runtime.crash_times)
        if attempt > CRASH_RESTART_LIMIT:
            runtime.state = "crashed"
            message = (
                f"The server crashed {attempt} times in {CRASH_WINDOW_SEC // 60} minutes; automatic restart paused. "
                "Check the console log, fix the problem and press Start."
            )
            append_console_line(f"[server] {message}")
            notify("server", "Minecraft server is DOWN (auto-restart paused)", message)
            return
        message = (
            f"The server crashed (exit code {code}); restarting in {CRASH_RESTART_DELAY_SEC} s "
            f"(attempt {attempt} of {CRASH_RESTART_LIMIT})."
        )
        append_console_line(f"[server] {message}")
        notify("server", "Minecraft server crashed", message)
        timer = threading.Timer(CRASH_RESTART_DELAY_SEC, restart_after_crash)
        timer.daemon = True
        timer.start()

    def launch_when_free(reason: str) -> bool:
        """For background callers (crash timer, autostart): wait for the lifecycle lock, then start
        the server unless a stop was requested or something else already started it."""
        if not runtime.lifecycle.acquire(timeout=LIFECYCLE_WAIT_SEC):
            raise RuntimeError(f"another server operation is still running; {reason} skipped")
        try:
            if runtime.stop_requested and reason == "crash restart":
                return False  # the user stopped the server in the meantime
            if server_running():
                return False
            launch_server()
            return True
        finally:
            runtime.lifecycle.release()

    def restart_after_crash() -> None:
        try:
            restarted = launch_when_free("crash restart")
        except Exception as exc:
            runtime.set_state("stopped", "idle")
            append_console_line(f"[server] automatic restart failed: {exc}")
            notify("server", "Minecraft server is DOWN (restart failed)", f"Automatic restart failed: {exc}")
            return
        if restarted:
            notify("server", "Minecraft server is back online", "The server was restarted automatically after a crash.")

    def launch_server(payload: dict[str, Any] | None = None) -> str:
        """Install if needed and start the Minecraft server. Caller holds runtime.lifecycle."""
        config = save_payload(payload)
        loader = str(config.get("loader", runtime.loader))
        version = str(config.get("version", runtime.version))
        runtime.loader = loader
        runtime.version = version
        runtime.set_state("starting", "starting")
        runtime.stop_requested = False

        process: Any = None

        def on_exit(code: int) -> None:
            append_console_line(f"[server] exited with code {code}")
            if runtime.process is not process:
                return  # a newer process already replaced this one (e.g. after a restart)
            runtime.set_state("stopped", "idle")
            runtime.process = None
            runtime.clear_players()
            if code == 0 or runtime.stop_requested:
                return
            if _effective_config().get("auto_restart"):
                handle_crash(code)
            else:
                notify("server", "Minecraft server stopped unexpectedly",
                       f"The server exited with code {code}. Automatic restart is off.")

        def on_output(line: str) -> None:
            runtime.track_player_event(line)
            append_console_line(line)

        target_dir = ensure_server_installed(config)
        runtime.set_state("starting", "starting")
        update_properties(target_dir, **_server_properties_from_config(config))

        java_exe = java_manager.ensure_java(java_manager.required_java(version),
                                            log=lambda message: append_console_line(f"[java] {message}"))
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
        runtime.set_state("running", "idle")
        append_console_line(f"[server] started with {loader} {version}")
        return "Server started."

    def request_stop() -> str:
        runtime.stop_requested = True  # also cancels a pending crash restart
        if runtime.process is None or not runtime.process.is_running():
            runtime.set_state("stopped", "idle")
            return "Server is already stopped."
        runtime.set_state("stopping", "stopping")
        runtime.process.stop()
        return "Stop requested."

    def stop_and_wait() -> None:
        """Stop the server and wait for the process to exit (kill it if it hangs). Caller holds the lock."""
        process = runtime.process
        request_stop()
        if process is not None and not _wait_for_exit(process, RESTART_STOP_TIMEOUT_SEC):
            append_console_line("[server] did not stop in time; killing the process")
            process.kill()
            _wait_for_exit(process, 10)

    @app.post("/api/server/start")
    def start_server(response: Response, payload: dict[str, Any] | None = None,
                     _: str = Depends(require_auth)) -> dict[str, Any]:
        if server_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}
        save_payload(payload)
        runtime.crash_times = []  # a manual start re-arms automatic crash restarts
        start_lifecycle_job("starting", launch_server, "Server start failed", state="starting")
        return accepted(response, "Starting the server…")

    @app.post("/api/server/stop")
    def stop_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if not runtime.lifecycle.acquire(blocking=False):
            raise busy_error()
        try:
            message = request_stop()
        finally:
            runtime.lifecycle.release()
        return {"state": runtime.state, "operation": runtime.operation, "message": message}

    def restart_job() -> str:
        stop_and_wait()
        return launch_server()

    @app.post("/api/server/restart")
    def restart_server(response: Response, _: str = Depends(require_auth)) -> dict[str, Any]:
        start_lifecycle_job("restarting", restart_job, "Server restart failed")
        return accepted(response, "Restarting the server…")

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
        """Zip the active world (and its Paper dimension folders)."""
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        folder = _active_world(config)
        if not os.path.isdir(os.path.join(target_dir, folder)):
            return {"ok": False, "message": f"The active world '{folder}' has not been generated yet; start the server once."}
        if runtime.process is not None and runtime.process.is_running():
            runtime.process.send("save-all flush")  # write pending chunks before copying
            time.sleep(2)
        archive = os.path.join(target_dir, f"world-backup-{folder}-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        worlds.export_zip(target_dir, folder, archive)
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
        if not runtime.lifecycle.acquire(blocking=False):
            raise busy_error()
        try:
            return restore_backup_locked(payload)
        finally:
            runtime.lifecycle.release()

    def restore_backup_locked(payload: dict[str, Any]) -> dict[str, Any]:
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
                raise HTTPException(status_code=400, detail=f"Backup contains files outside a world folder: {unsafe[0]}")
            # Move the current copy of each world folder in the backup aside instead of overwriting it.
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for root in sorted({m.replace("\\", "/").split("/")[0] for m in members}):
                current = os.path.join(target_dir, root)
                if os.path.isdir(current):
                    os.rename(current, os.path.join(target_dir, f"{root}-pre-restore-{stamp}"))
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
        project_ids = []
        for base_dir in (mods_dir, plugins_dir):
            if not os.path.isdir(base_dir):
                continue
            by_file = {info["filename"]: (pid, info) for pid, info in modrinth.installed_projects(base_dir).items()}
            for name in sorted(os.listdir(base_dir)):
                if name.lower().endswith(".jar"):
                    pid, info = by_file.get(name, (None, {}))
                    if pid:
                        project_ids.append(pid)
                    items.append({"name": name, "path": os.path.join(base_dir, name),
                                  "folder": os.path.basename(base_dir), "title": info.get("title", ""),
                                  "project_id": pid, "version": info.get("version", "")})
        return {"mods": items, "project_ids": project_ids}

    @app.delete("/api/server/mods/{name}")
    def remove_mod(name: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Stop the server before removing mods (they are in use).")
        name = os.path.basename(name)
        target_dir = _server_dir_for_config(_effective_config())
        for folder in ("mods", "plugins"):
            path = os.path.join(target_dir, folder, name)
            if name.lower().endswith(".jar") and os.path.isfile(path):
                os.remove(path)
                modrinth.forget_file(os.path.dirname(path), name)
                return {"ok": True, "message": f"Removed {name}."}
        raise HTTPException(status_code=404, detail=f"{name} is not installed.")

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

    # ---------- Worlds ----------
    def server_running() -> bool:
        return bool(runtime.process is not None and runtime.process.is_running())

    def worlds_payload(message: str = "") -> dict[str, Any]:
        config = _effective_config()
        active = _active_world(config)
        return {
            "worlds": worlds.list_worlds(_server_dir_for_config(config), active),
            "active": active,
            "server_running": server_running(),
            "message": message,
        }

    def checked_world(folder: str) -> str:
        if not worlds.valid_folder(folder):
            raise HTTPException(status_code=400, detail="Invalid world.")
        return _server_dir_for_config(_effective_config())

    @app.get("/api/worlds")
    def list_worlds(_: str = Depends(require_auth)) -> dict[str, Any]:
        return worlds_payload()

    @app.post("/api/worlds")
    def create_world(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Give the new world a name.")
        folder = worlds.create(_server_dir_for_config(_effective_config()), name, str(payload.get("seed", "")).strip())
        return worlds_payload(f"Created '{name}'. Switch to it to generate it.") | {"folder": folder}

    @app.post("/api/worlds/upload")
    def upload_world(file: UploadFile = File(...), name: str = Form(default=""), _: str = Depends(require_auth)) -> dict[str, Any]:
        filename = os.path.basename(file.filename or "world.zip")
        if not filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="Upload the world as a .zip file.")
        sdir = _server_dir_for_config(_effective_config())
        paths.ensure_dirs()
        temp_path = os.path.join(paths.DATA_DIR, f"upload-{int(time.time() * 1000)}.zip")
        try:
            with open(temp_path, "wb") as handle:
                while chunk := file.file.read(1 << 20):
                    handle.write(chunk)
            display_name = name.strip() or os.path.splitext(filename)[0]
            try:
                folder = worlds.import_zip(sdir, temp_path, display_name)
            except (zipfile.BadZipFile, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc) or "Not a valid zip file.") from exc
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return worlds_payload(f"Imported '{display_name}'.") | {"folder": folder}

    @app.post("/api/worlds/{folder}/activate")
    def activate_world(folder: str, response: Response, _: str = Depends(require_auth)) -> dict[str, Any]:
        sdir = checked_world(folder)
        known = {w["folder"]: w for w in worlds.list_worlds(sdir, _active_world(_effective_config()))}
        if folder not in known:
            raise HTTPException(status_code=404, detail="World not found.")
        if known[folder]["active"]:
            return worlds_payload("That world is already active.")
        name = known[folder]["name"]

        def select_world() -> None:
            config = _effective_config()
            config["level_name"] = folder
            if not known[folder]["exists"]:
                config["level_seed"] = known[folder].get("seed", "")  # only used when the world is generated
            config_store.save(config)

        if server_running():
            def switch_and_restart() -> str:
                stop_and_wait()
                select_world()
                launch_server()
                return f"Switched to '{name}' and restarted the server."

            start_lifecycle_job("switching world", switch_and_restart, "World switch failed", state="stopping")
            response.status_code = 202
            return worlds_payload(f"Switching to '{name}'; the server restarts in the background.")
        if not runtime.lifecycle.acquire(blocking=False):
            raise busy_error()
        try:
            select_world()
        finally:
            runtime.lifecycle.release()
        return worlds_payload(f"Switched to '{name}'. It loads the next time the server starts.")

    @app.post("/api/worlds/{folder}/rename")
    def rename_world(folder: str, payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        sdir = checked_world(folder)
        try:
            worlds.rename(sdir, folder, str(payload.get("name", "")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return worlds_payload("World renamed.")

    @app.delete("/api/worlds/{folder}")
    def delete_world(folder: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        sdir = checked_world(folder)
        if folder == _active_world(_effective_config()):
            raise HTTPException(status_code=409, detail="Switch to another world before deleting this one.")
        worlds.delete(sdir, folder)
        return worlds_payload("World deleted.")

    @app.get("/api/worlds/{folder}/download")
    def download_world(folder: str, _: str = Depends(require_auth)) -> FileResponse:
        sdir = checked_world(folder)
        if server_running() and folder == _active_world(_effective_config()):
            runtime.process.send("save-all flush")
            time.sleep(2)
        paths.ensure_dirs()
        archive = os.path.join(paths.DATA_DIR, f"export-{folder}-{int(time.time())}.zip")
        try:
            worlds.export_zip(sdir, folder, archive)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(archive, media_type="application/zip", filename=f"{folder}.zip",
                            background=BackgroundTask(os.remove, archive))

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

    def modrinth_context() -> tuple[str, str]:
        config = _effective_config()
        loader = str(config.get("loader", "vanilla"))
        if loader == "vanilla":
            raise HTTPException(status_code=400, detail="Vanilla servers can't use mods or plugins. Choose Paper, "
                                                        "Fabric, Forge or NeoForge in Server setup.")
        return loader, str(config.get("version", "1.21.7"))

    def upstream_error(exc: Exception) -> HTTPException:
        return HTTPException(status_code=502, detail=f"Modrinth is not reachable right now: {exc}")

    @app.get("/api/modrinth/search")
    def modrinth_search(query: str = "", sort: str = "relevance", category: str = "", offset: int = 0,
                        _: str = Depends(require_auth)) -> dict[str, Any]:
        """Search, or browse the most popular projects when the query is empty."""
        loader, version = modrinth_context()
        try:
            result = modrinth.search(query.strip(), loader, version, limit=MODRINTH_PAGE_SIZE,
                                     offset=max(0, offset), sort=sort, category=category.strip())
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc
        return {**result, "items": result["hits"], "loader": loader, "version": version}

    @app.get("/api/modrinth/categories")
    def modrinth_categories(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            return {"categories": modrinth.categories()}
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc

    @app.get("/api/modrinth/project/{project_ref}")
    def modrinth_project(project_ref: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        loader, version = modrinth_context()
        try:
            return modrinth.project(project_ref, loader, version)
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc

    @app.post("/api/modrinth/install")
    def modrinth_install(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        project_ref = str(payload.get("slug") or payload.get("project_id") or payload.get("id") or "").strip()
        if not project_ref:
            raise HTTPException(status_code=400, detail="A Modrinth project slug or ID is required.")
        config = _effective_config()
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_folder = _content_dir_for_config(config)
        before = set(modrinth.installed_projects(target_folder))
        try:
            installed = modrinth.install(project_ref, loader, version, target_folder, log=append_console_line)
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        added = [info["title"] for pid, info in modrinth.installed_projects(target_folder).items() if pid not in before]
        dependencies = added[1:] if len(added) > 1 else []
        message = f"Installed {installed}."
        if dependencies:
            message += f" Also installed required: {', '.join(dependencies)}."
        return {"ok": True, "filename": installed, "dependencies": dependencies, "message": message}

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
        cancel_playit_restart()  # this start supersedes any pending automatic restart
        key_rejected = threading.Event()
        if not (runtime.playit_agent and runtime.playit_agent.is_running()):
            playit.ensure_agent(log=playit_log)
            playit.kill_stale_agents(log=playit_log)  # only one agent may run per device
            agent: Any = None
            last_error = [""]

            def on_agent_output(line: str) -> None:
                append_console_line(line)
                if "error" in line.lower():
                    last_error[0] = line.strip()
                if key_rejected.is_set() or not playit.is_key_rejected(line):
                    return
                # A rejected key never recovers by retrying: stop the agent (deliberately, so it is not
                # restarted as a crash) and ask the owner to link the launcher again.
                key_rejected.set()
                runtime.playit_setup = "error"
                runtime.playit_error = PLAYIT_KEY_REJECTED
                playit_log(PLAYIT_KEY_REJECTED)
                notify("tunnels", "PlayIt key rejected", PLAYIT_KEY_REJECTED)
                if runtime.playit_agent is agent:
                    stop_playit_agent()
                    runtime.playit_state = "stopped"

            def on_agent_exit(code: int) -> None:
                # Deliberate stops clear runtime.playit_agent first, so only a crash still finds itself here.
                if runtime.playit_agent is not agent:
                    append_console_line(f"[playit] exited with code {code}")
                    return
                _playit_on_exit(app.state.console_manager, runtime, code)
                if runtime.shutting_down:
                    return
                now = time.monotonic()
                runtime.playit_crash_times = [t for t in runtime.playit_crash_times if now - t < PLAYIT_CRASH_WINDOW_SEC]
                runtime.playit_crash_times.append(now)
                if len(runtime.playit_crash_times) > PLAYIT_CRASH_LIMIT:
                    message = (f"The PlayIt agent stopped {len(runtime.playit_crash_times)} times in "
                               f"{PLAYIT_CRASH_WINDOW_SEC // 60} minutes, so automatic restarts are paused. "
                               f"Last error: {last_error[0] or f'exit code {code}'}. Click Start tunnel to try again.")
                    runtime.playit_setup = "error"
                    runtime.playit_error = message
                    playit_log(message)
                    notify("tunnels", "PlayIt tunnel DOWN (auto-restart paused)", message)
                    return
                message = f"The PlayIt agent exited (code {code}); restarting it in {PLAYIT_RETRY_SEC} s."
                playit_log(message)
                notify("tunnels", "PlayIt tunnel stopped unexpectedly", message)
                schedule_playit_restart(secret)

            agent = playit.PlayitAgent(on_output=on_agent_output, on_exit=on_agent_exit)
            runtime.playit_agent = agent
            agent.start(secret)

        def should_abort() -> str | None:
            if key_rejected.is_set():
                return PLAYIT_KEY_REJECTED
            if not (runtime.playit_agent and runtime.playit_agent.is_running()):
                return "The PlayIt agent stopped before the tunnel was ready."
            return None

        config = _effective_config()
        address = playit.ensure_tunnel(secret, int(config.get("server_port", 25565)), playit_log,
                                       should_abort=should_abort)
        playit.save_config(secret, address)
        runtime.playit_state = "running"
        runtime.playit_address = address

    def schedule_playit_restart(secret: str) -> None:
        """Restart the agent after PLAYIT_RETRY_SEC; replaces any restart already pending."""
        cancel_playit_restart()
        timer = threading.Timer(PLAYIT_RETRY_SEC, lambda: run_in_background("PlayIt tunnel", lambda: start_agent_and_tunnels(secret)))
        timer.daemon = True
        runtime.playit_restart_timer = timer
        timer.start()

    def cancel_playit_restart() -> None:
        timer = runtime.playit_restart_timer
        runtime.playit_restart_timer = None
        if timer is not None:
            timer.cancel()

    def stop_playit_agent() -> None:
        """Deliberate stop: forget the agent first so its exit is not treated as a crash."""
        cancel_playit_restart()
        agent = runtime.playit_agent
        runtime.playit_agent = None
        if agent is not None and agent.is_running():
            agent.stop()

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
            runtime.playit_claim_url = None
            if runtime.playit_cancel:
                runtime.playit_setup = "idle"
                playit_log("Setup cancelled.")
                return
            runtime.playit_setup = "error"
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
            stop_playit_agent()
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
        if runtime.playit_setup == "error":  # a new attempt clears the previous failure
            runtime.playit_setup = "idle"
            runtime.playit_error = ""
        runtime.playit_crash_times.clear()  # a manual start re-arms automatic restarts
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
        stop_playit_agent()
        cfg = playit.load_config()
        runtime.playit_state = "stopped"
        runtime.playit_address = cfg.get("address") or runtime.playit_address
        return playit_info("PlayIt tunnel stopped.")

    @app.get("/api/playit/config")
    def playit_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        # Never expose the secret key over HTTP; it stays in playit_config.json on the host.
        cfg = playit.load_config()
        return {"address": cfg.get("address") or "", "secret_present": bool(cfg.get("secret"))}

    @app.put("/api/playit/config")
    def save_playit_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        """Edit playit_config.json by hand: paste a secret key from playit.gg or fix the address.

        A blank or missing secret keeps the saved one (it is never sent to the browser).
        Cancels a pending approval; a changed secret stops the running agent.
        """
        unknown = set(payload) - {"secret", "address"}
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown keys: {', '.join(sorted(unknown))}. Allowed: secret, address.")
        secret = payload.get("secret") or ""
        address = payload.get("address") or ""
        if not isinstance(secret, str) or not isinstance(address, str):
            raise HTTPException(status_code=400, detail="secret and address must be text.")
        if runtime.playit_setup == "connecting":
            raise HTTPException(status_code=409, detail="PlayIt is connecting; wait for it to finish, then save.")
        if runtime.playit_setup == "awaiting-approval":
            runtime.playit_cancel = True
            runtime.playit_setup = "idle"
            runtime.playit_claim_url = None
        runtime.playit_error = ""

        saved = playit.load_config().get("secret") or ""
        secret = secret.strip() or saved
        if not secret:
            raise HTTPException(status_code=400, detail="No saved secret key: paste one into \"secret\".")
        if secret != saved and not playit.is_valid_secret(secret):
            raise HTTPException(status_code=400, detail="That is not a playit secret key: it should be the long "
                                                        "hexadecimal key (0-9, a-f) from playit.gg.")
        if secret != saved:
            stop_playit_agent()
            runtime.playit_state = "stopped"
        playit.save_config(secret, address.strip())
        runtime.playit_address = address.strip() or runtime.playit_address
        playit_log("PlayIt config saved from the dashboard.")
        return playit_info("PlayIt config saved. Click Start tunnel to use it.")

    # ---------- Remote dashboard (Cloudflare quick tunnel) ----------
    def web_log(line: str) -> None:
        append_console_line(f"[cloudflare] {line}")

    def remote_info(message: str = "") -> dict[str, Any]:
        return {
            "enabled": bool(_effective_config().get("remote_dashboard")),
            "state": runtime.web_tunnel_state,
            "url": runtime.web_tunnel_url or "",
            "error": runtime.web_tunnel_error,
            "weak_password": password_is_weak(),
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
            if url != notifier.load_config().get("last_link"):
                notifier.save_config({"last_link": url})
                notify("link", "New dashboard link", f"Your dashboard is now at:\n\n{url}\n\n"
                       "The old link no longer works. New links can take up to a minute to open.", throttle=False)

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
            notify("tunnels", "Dashboard link went down",
                   f"{runtime.web_tunnel_error}. You will get the new link by email when it is back.")
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
            notify("tunnels", "Dashboard link could not start", str(exc))

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
                category = "server" if name == "Minecraft server" else "tunnels"
                notify(category, f"{name} failed to start", str(exc))

        threading.Thread(target=worker, name=f"autostart-{name}", daemon=True).start()

    def autostart() -> None:
        """Bring up what the Always-on settings ask for. EZ_NO_AUTOSTART=1 skips it (debugging)."""
        if _effective_config().get("remote_dashboard") and password_is_weak():
            warning = ("[auth] WARNING: the remote dashboard link is on and EZ_WEBAPP_PASSWORD is shorter than "
                       "12 characters. Anyone with the link can try to guess it; set a longer password.")
            logging.getLogger("uvicorn.error").warning(warning)
            append_console_line(warning)
        if os.getenv("EZ_NO_AUTOSTART") == "1":
            return
        threading.Thread(target=heartbeat_loop, name="heartbeat", daemon=True).start()
        threading.Thread(target=update_check_loop, name="update-check", daemon=True).start()
        config = _effective_config()
        resume = take_resume_marker()  # what was running before an in-app update restarted us
        secret = playit.load_config().get("secret")
        if (config.get("autostart_playit") or resume.get("playit")) and secret:
            append_console_line("[autostart] starting the PlayIt tunnel")
            run_in_background("PlayIt tunnel", lambda: start_agent_and_tunnels(secret))
        if config.get("autostart_server") or resume.get("server"):
            append_console_line("[autostart] starting the Minecraft server")
            run_in_background("Minecraft server", lambda: launch_when_free("autostart"))
        if config.get("remote_dashboard"):
            append_console_line("[autostart] starting the remote dashboard link")
            run_in_background("remote dashboard", start_web_tunnel_safely)

    def shutdown() -> None:
        """Stop Minecraft and the PlayIt agent with the dashboard, so no orphan keeps the port or world lock."""
        runtime.shutting_down = True
        runtime.stop_requested = True
        process = runtime.process
        if process is not None and process.is_running():
            append_console_line("[server] dashboard shutting down; stopping the server")
            process.stop()
            if not _wait_for_exit(process, SHUTDOWN_STOP_TIMEOUT_SEC):
                process.kill()
        stop_playit_agent()
        stop_web_tunnel()

    # ---------- Self-update ----------
    def update_log(line: str) -> None:
        append_console_line(f"[update] {line}")

    def resume_marker_path() -> str:
        return os.path.join(paths.DATA_DIR, "resume_after_update.json")

    def take_resume_marker() -> dict[str, Any]:
        """Read and delete the marker written before an update restart."""
        path = resume_marker_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        try:
            os.remove(path)
        except OSError:
            pass
        return data if isinstance(data, dict) else {}

    def update_payload(message: str = "") -> dict[str, Any]:
        config = _effective_config()
        return {
            **(runtime.update_info or {}),
            "state": runtime.update_state,
            "error": runtime.update_error,
            "message": message or runtime.update_message,
            "auto_check": bool(config.get("auto_update_check")),
            "auto_install": bool(config.get("auto_update_install")),
        }

    def check_for_updates(fetch: bool = True) -> dict[str, Any]:
        runtime.update_info = updater.status(fetch=fetch)
        return runtime.update_info

    def run_update() -> None:
        """Pull, then restart onto the new code (resuming Minecraft/PlayIt if they were running)."""
        runtime.update_state = "updating"
        runtime.update_error = ""
        runtime.update_message = "Downloading the update..."
        try:
            result = updater.apply(update_log)
        except Exception as exc:
            runtime.update_state = "error"
            runtime.update_error = str(exc)
            update_log(f"Update failed: {exc}")
            notify("updates", "Update failed", str(exc))
            return
        if not result.get("updated"):
            runtime.update_state = "idle"
            runtime.update_message = "Already up to date."
            return
        resume = {
            "server": bool(runtime.process is not None and runtime.process.is_running()),
            "playit": bool(runtime.playit_agent is not None and runtime.playit_agent.is_running()),
        }
        paths.ensure_dirs()
        with open(resume_marker_path(), "w", encoding="utf-8") as handle:
            json.dump(resume, handle)
        runtime.update_state = "restarting"
        runtime.update_message = f"Updated {result['from']} -> {result['to']}; restarting the dashboard..."
        update_log(runtime.update_message)
        time.sleep(1)  # let the browser receive the "restarting" state
        shutdown()
        updater.restart_process()

    def update_check_loop() -> None:
        time.sleep(UPDATE_FIRST_CHECK_SEC)
        while not runtime.shutting_down:
            config = _effective_config()
            if config.get("auto_update_check") and runtime.update_state in {"idle", "error"}:
                try:
                    info = check_for_updates()
                except Exception as exc:
                    update_log(f"Update check failed: {exc}")
                    info = {}
                if info.get("behind") and info.get("latest_hash") != runtime.update_notified_hash:
                    runtime.update_notified_hash = info["latest_hash"]
                    changes = "\n".join(f"- {c['subject']} ({c['when']})" for c in info.get("commits", []))
                    notify("updates", "Update available", f"{info['behind']} new change(s) on {info['upstream']}:\n\n"
                           f"{changes}\n\nOpen Server setup > Updates to install.", throttle=False)
                busy = runtime.state in {"starting", "stopping", "installing"} or bool(runtime.online_players)
                if info.get("behind") and config.get("auto_update_install") and not busy and not info.get("local_changes"):
                    update_log("Installing the update automatically (no one is online).")
                    run_update()
            time.sleep(UPDATE_CHECK_INTERVAL_SEC)

    @app.get("/api/update")
    def update_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.update_info is None:
            try:
                check_for_updates(fetch=False)
            except Exception as exc:
                runtime.update_info = {"available": False, "reason": str(exc)}
        return update_payload()

    @app.post("/api/update/check")
    def update_check(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            info = check_for_updates(fetch=True)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Update check failed: {exc}") from exc
        if not info.get("available"):
            return update_payload(info.get("reason", ""))
        if info["behind"]:
            return update_payload(f"{info['behind']} update(s) available.")
        return update_payload("You are on the latest version.")

    @app.post("/api/update/apply")
    def update_apply(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.update_state in {"updating", "restarting"}:
            return update_payload("An update is already running.")
        runtime.update_state = "updating"
        threading.Thread(target=run_update, name="update", daemon=True).start()
        return update_payload("Updating; the dashboard will restart by itself.")

    @app.get("/api/notifications")
    def get_notifications(_: str = Depends(require_auth)) -> dict[str, Any]:
        return notifier.public_config()

    @app.put("/api/notifications")
    def save_notifications(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        payload.pop("last_link", None)
        notifier.save_config(payload)
        return notifier.public_config()

    @app.post("/api/notifications/test")
    def test_notifications(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = notifier.load_config()
        results = []
        if config.get("email_to") and config.get("smtp_user") and config.get("smtp_password"):
            try:
                notifier.send_email(config, "[EZ Minecraft] Test alert",
                                    "Alerts are working. You will get an email like this when the server "
                                    "crashes, the dashboard link changes, or a tunnel has a problem.")
                results.append("Test email sent.")
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Email failed: {exc}") from exc
        if config.get("heartbeat_url"):
            if not notifier.ping_heartbeat(config["heartbeat_url"]):
                raise HTTPException(status_code=502, detail="Heartbeat URL did not answer.")
            results.append("Heartbeat ping OK.")
        if not results:
            raise HTTPException(status_code=400, detail="Fill in the email settings or a heartbeat URL first.")
        return {"ok": True, "message": " ".join(results)}

    app.state.autostart = autostart
    app.state.shutdown = shutdown
    app.state.restart_after_crash = restart_after_crash
    app.state.notify = notify

    @app.websocket("/ws/console")
    async def console_websocket(websocket: WebSocket) -> None:
        state = app.state
        try:
            authenticate(websocket, state.sessions, state.login_limiter, state.on_lockout)
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
