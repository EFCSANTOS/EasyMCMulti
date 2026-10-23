"""Constants and module-level helpers shared by the routers (config, paths, metrics).

Routers use these as `common.NAME` so tests can monkeypatch them in one place.
"""
from __future__ import annotations

import datetime
import glob
import logging
import os
import time
from typing import Any

from fastapi import HTTPException

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - installed in project venv for runtime metrics
    psutil = None

from core import backups, config_store, downloaders, paths, worlds
# Re-exported for the routers (common.ServerProcess, …) so tests can patch them here.
from core.server_process import ServerProcess, accept_eula, update_properties  # noqa: F401
from webapp.state import RuntimeState
from webapp import properties_meta
from webapp.validation import clean_stored_config

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
RESTART_STOP_TIMEOUT_SEC = 60
SHUTDOWN_STOP_TIMEOUT_SEC = 45
# Crash auto-restart: wait CRASH_RESTART_DELAY_SEC, at most CRASH_RESTART_LIMIT times per CRASH_WINDOW_SEC.
CRASH_RESTART_DELAY_SEC = 10
CRASH_RESTART_LIMIT = 3
CRASH_WINDOW_SEC = 600
LOADER_LABELS = {"vanilla": "Vanilla", "paper": "Paper", "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge"}
# How often the scheduler (backups, daily restart, idle stop) checks; how long a backup waits for
# the server to confirm "save-all flush" before zipping anyway.
SCHEDULER_TICK_SEC = 60
# Mod updates (FEAT-07) keep the old jars until the updated server has run this long.
OLD_MODS_KEEP_SEC = 300
BACKUP_SAVE_TIMEOUT_SEC = 30
# Friends Discord channel: joins/leaves within this window are sent as one message.
FRIENDS_BATCH_SEC = 30
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
HEARTBEAT_INTERVAL_SEC = 60
_APP_START_TIME = time.time()
FALLBACK_VERSIONS = {
    "vanilla": ["1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.20.4"],
    "paper": ["1.21.7", "1.21.6", "1.21.4", "1.21.1", "1.20.4"],
    "fabric": ["1.21.7", "1.21.6", "1.21.4", "1.20.4"],
    "forge": ["1.20.1", "1.19.2", "1.16.5"],
    "neoforge": ["1.21.7", "1.21.6", "1.21.1", "1.20.1"],
}

def _server_properties_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """Values written to server.properties on every start: the owner's extra properties (FEAT-14)
    first, then the keys the Settings form manages, which always win."""
    extra = config.get("extra_properties") or {}
    managed = {
        "server-port": int(config.get("server_port", 25565)),
        "online-mode": bool(config.get("online_mode", True)),
        "max-players": int(config.get("max_players", 10)),
        "motd": properties_meta.escape_value(str(config.get("motd", "EZ Minecraft Server"))),
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
    if isinstance(extra, dict):
        extra = {key: properties_meta.escape_value(str(value)) for key, value in extra.items()
                 if key not in managed and key not in properties_meta.LOCKED}
        return {**extra, **managed}
    return managed


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
        # Remote link mode (FEAT-17): "quick" (random trycloudflare.com link, no account) or "named"
        # (the owner's Cloudflare tunnel token + hostname: the same address after every restart).
        # Bedrock crossplay (FEAT-18): Geyser + Floodgate, Geyser's UDP port.
        "bedrock_enabled": False,
        "bedrock_port": 19132,
        "remote_mode": "quick",
        "remote_hostname": "",
        # Public status page /s/<token> (FEAT-03): off by default; the token is generated by
        # POST /api/status-page and never accepted from PUT /api/config.
        "status_page_enabled": False,
        "status_page_token": "",
        "status_page_show_names": True,
        "status_page_show_lan": False,
        # Backups (FEAT-01): every N hours while the world changes (0 = off); retention.
        "backup_interval_hours": 6,
        "backup_keep_recent": 8,
        "backup_keep_daily": 7,
        "backup_prune_manual": False,
        # Schedule (FEAT-06): daily restart "HH:MM" ("" = off), stop after N empty minutes (0 = off),
        # quiet hours "HH:MM"–"HH:MM" without autostart / crash restart ("" = off).
        # Performance (FEAT-13): tuned GC flags on by default; extra JVM arguments for advanced users.
        # Friend accounts (FEAT-10): may friends stop/restart the server? (They can always start it.)
        "friends_can_stop": False,
        "jvm_preset_flags": True,
        "jvm_extra_args": "",
        # Raw server.properties keys the Settings form doesn't cover (FEAT-14): {"spawn-protection": "0"}.
        "extra_properties": {},
        "restart_daily_time": "",
        "idle_stop_minutes": 0,
        "quiet_hours_start": "",
        "quiet_hours_end": "",
    }


_host_ram_cache: list[int | None] = []
# Where warnings about invalid saved config values go (create_app adds the dashboard console).
_config_warning_sinks: list[Any] = []
_config_warned: set[tuple[str, str]] = set()


def _host_ram_mb() -> int | None:
    """Total memory of this machine in MB (None if unknown); the upper bound for ram_mb."""
    if not _host_ram_cache:
        total = _memory_usage()[1]
        _host_ram_cache.append(total // (1024 * 1024) if total > 1 else None)
    return _host_ram_cache[0]


def _warn_bad_config(key: str, value: Any) -> None:
    marker = (key, repr(value))
    if marker in _config_warned:
        return  # _effective_config runs on every poll; say it once
    _config_warned.add(marker)
    message = f"[config] Ignoring invalid saved value {key}={value!r}; using the default instead."
    logging.getLogger("uvicorn.error").warning(message)
    for sink in _config_warning_sinks:
        sink(message)


def _effective_config() -> dict[str, Any]:
    """Defaults + saved config. Invalid saved values (old or hand-edited files) fall back to defaults."""
    config = config_store.load()
    merged = _default_config()
    if isinstance(config, dict):
        merged.update(clean_stored_config(config, merged, _host_ram_mb(), _warn_bad_config))
    return merged


def _system_metrics_snapshot() -> dict[str, Any]:
    # Each source is tried separately: Android blocks /proc/stat (CPU) but allows /proc/meminfo.
    cpu, scope = _cpu_usage()
    used, total = _memory_usage()
    return {
        "cpu": cpu,
        "cpuScope": scope,
        "ram": int(used),
        "ramTotal": int(total),
        "uptime": int(time.time() - _APP_START_TIME),
    }


PROC_DIR = "/proc"
# Previous counter reading per source, so each poll reports the use since the last one.
_cpu_last: dict[str, tuple[float, float]] = {}


def _cpu_usage() -> tuple[float, str]:
    """(percent, scope). Scope is "system", "processes" (only our own processes) or "unavailable".

    psutil (absent on Termux) and /proc/stat give the whole machine. Android blocks /proc/stat for
    apps but still lets them read /proc/<pid>/stat of their own processes, so there we add up the
    CPU time of everything Termux runs (the Minecraft server included).
    """
    if psutil is not None:
        try:
            return float(psutil.cpu_percent(interval=None)), "system"
        except Exception:
            pass
    try:
        return _cpu_delta("system", *_proc_stat_ticks()), "system"
    except (OSError, ValueError, IndexError):
        pass
    try:
        busy = _own_process_ticks()
    except OSError:
        return 0.0, "unavailable"
    elapsed_ticks = time.monotonic() * _clock_ticks() * (os.cpu_count() or 1)
    return _cpu_delta("processes", busy, elapsed_ticks), "processes"


def _cpu_delta(source: str, busy: float, total: float) -> float:
    previous = _cpu_last.get(source)
    _cpu_last[source] = (busy, total)
    if previous is None or total <= previous[1]:
        return 0.0  # first reading: nothing to compare with yet
    percent = (busy - previous[0]) / (total - previous[1]) * 100
    return round(min(100.0, max(0.0, percent)), 1)


def _proc_stat_ticks() -> tuple[float, float]:
    """(busy, total) clock ticks of all CPUs from the first line of /proc/stat."""
    with open(os.path.join(PROC_DIR, "stat"), "r", encoding="utf-8") as handle:
        fields = [float(value) for value in handle.readline().split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0.0)  # idle + iowait
    total = sum(fields[:8])  # guest time is already counted in user/nice
    return total - idle, total


def _own_process_ticks() -> float:
    """User + system clock ticks of every process whose /proc/<pid>/stat we may read."""
    busy = 0.0
    for name in os.listdir(PROC_DIR):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(PROC_DIR, name, "stat"), "r", encoding="utf-8") as handle:
                # The command name (field 2) may contain spaces, so split after its closing ")".
                fields = handle.read().rsplit(")", 1)[1].split()
            busy += float(fields[11]) + float(fields[12])  # utime, stime
        except (OSError, ValueError, IndexError):
            continue  # exited meanwhile, or not ours
    return busy


def _clock_ticks() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100


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
    return name.startswith(backups.PREFIX) and name.endswith(".zip")


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


def _in_quiet_hours(config: dict[str, Any], now: Any = None) -> bool:
    """True between quiet_hours_start and quiet_hours_end (host local time; may wrap past midnight)."""
    start, end = str(config.get("quiet_hours_start") or ""), str(config.get("quiet_hours_end") or "")
    if not start or not end or start == end:
        return False
    current = (now or datetime.datetime.now()).strftime("%H:%M")
    return start <= current < end if start < end else current >= start or current < end


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
