from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
import time
from typing import Any

# Vanilla/Paper/Fabric/Forge log format: "[12:00:00] [Server thread/INFO]: Steve joined the game".
# Requiring the name right after "]: " keeps chat lines ("]: <Steve> ...") from matching.
_PLAYER_EVENT = re.compile(r"\]: (\w{1,16}) (joined|left) the game$")
# Names shown under "Recently seen" on the Players page (joined, or refused by the whitelist).
RECENT_PLAYERS_LIMIT = 20


@dataclass
class RuntimeState:
    loader: str = "vanilla"
    version: str = "unknown"
    state: str = "stopped"
    operation: str = "idle"
    install_status: str = "ready"
    install_progress: float = 0.0
    install_message: str = ""
    online_players: set[str] = field(default_factory=set)
    # name -> {"last_seen": epoch seconds, "rejected": refused by the whitelist}; in memory only.
    recent_players: dict[str, dict[str, Any]] = field(default_factory=dict)
    lan_address: str = "unknown"
    public_address: str | None = None
    playit_state: str = "unknown"
    playit_address: str | None = None
    playit_agent: Any = None
    # Background PlayIt setup: "idle" | "awaiting-approval" | "connecting" | "error".
    playit_setup: str = "idle"
    playit_claim_url: str | None = None
    playit_error: str = ""
    # Remote dashboard (Cloudflare quick tunnel): state "off" | "starting" | "running" | "error".
    web_tunnel: Any = None
    web_tunnel_url: str | None = None
    web_tunnel_state: str = "off"
    web_tunnel_error: str = ""
    web_tunnel_wanted: bool = False
    # Self-update: state "idle" | "checking" | "updating" | "restarting" | "error".
    update_state: str = "idle"
    update_error: str = ""
    update_message: str = ""
    update_info: dict[str, Any] | None = None
    update_notified_hash: str = ""
    playit_cancel: bool = False
    # One pending agent restart at most (threading.Timer); recent agent crashes (monotonic times).
    playit_restart_timer: Any = None
    playit_crash_times: list[float] = field(default_factory=list)
    process: Any = None
    # True once a stop was asked for (button, restart, console "stop", shutdown); a non-zero exit
    # without it counts as a crash. crash_times holds monotonic timestamps of recent crashes.
    stop_requested: bool = False
    shutting_down: bool = False
    # The world changed since the last backup (the server ran); the backup scheduler only acts then.
    backup_dirty: bool = False
    # FEAT-06: date (ISO) of today's daily restart once done, warnings already sent (minutes),
    # when the server became empty (epoch seconds; None while someone is online), and why it last
    # stopped ("idle" for the idle stop, shown on the overview; "" otherwise).
    daily_restart_done: str = ""
    restart_warnings_sent: set[int] = field(default_factory=set)
    empty_since: float | None = None
    stopped_reason: str = ""
    # FEAT-08: last crash diagnosis (list of {code, title, message, action}) and when it was made;
    # when the current/last server process started (to pick crash reports written since then).
    diagnosis: list[dict[str, Any]] = field(default_factory=list)
    diagnosis_at: float = 0.0
    last_start_at: float = 0.0
    # FEAT-07: last mod update check (full items incl. download URLs) and when it ran.
    mod_updates: list[dict[str, Any]] = field(default_factory=list)
    mod_updates_at: float = 0.0
    mod_updates_notified: str = ""
    crash_times: list[float] = field(default_factory=list)
    # Outcome of the last background lifecycle job (create/start/restart/world switch), for the UI.
    operation_error: str = ""
    operation_message: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)
    # Held for the whole of any operation that changes the server process (FIX-04): one at a time.
    lifecycle: threading.Lock = field(default_factory=threading.Lock)

    def set_state(self, state: str, operation: str | None = None) -> None:
        """Change state (and operation) together so snapshot() never sees half an update."""
        with self.lock:
            self.state = state
            if operation is not None:
                self.operation = operation

    def track_player_event(self, line: str) -> tuple[str, str] | None:
        """Update online_players from a server console line; returns (name, "joined"|"left") or None."""
        match = _PLAYER_EVENT.search(line)
        if not match:
            return None
        name, event = match.groups()
        with self.lock:
            if event == "joined":
                self.online_players.add(name)
                self.empty_since = None
            else:
                self.online_players.discard(name)
                if not self.online_players:
                    self.empty_since = time.time()
        if event == "joined":
            self.note_player(name, rejected=False)
        return name, event

    def note_player(self, name: str, rejected: bool) -> None:
        """Remember a player who joined or was refused, newest first, at most RECENT_PLAYERS_LIMIT."""
        with self.lock:
            self.recent_players.pop(name, None)
            self.recent_players[name] = {"last_seen": time.time(), "rejected": rejected}
            while len(self.recent_players) > RECENT_PLAYERS_LIMIT:
                self.recent_players.pop(next(iter(self.recent_players)))

    def recent(self) -> list[dict[str, Any]]:
        with self.lock:
            return [{"name": name, **info} for name, info in reversed(self.recent_players.items())]

    def clear_players(self) -> None:
        with self.lock:
            self.online_players.clear()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "loader": self.loader,
                "version": self.version,
                "players": len(self.online_players),
                "player_names": sorted(self.online_players),
                "lan_address": self.lan_address,
                "public_address": self.public_address,
                "operation": self.operation,
                "operation_error": self.operation_error,
                "operation_message": self.operation_message,
                "stopped_reason": self.stopped_reason,
                "diagnosis_at": self.diagnosis_at,
                "install_status": self.install_status,
                "install_progress": self.install_progress,
                "install_message": self.install_message,
                "playit": {
                    "state": self.playit_state,
                    "address": self.playit_address,
                },
            }
