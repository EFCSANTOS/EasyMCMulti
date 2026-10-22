from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
from typing import Any

# Vanilla/Paper/Fabric/Forge log format: "[12:00:00] [Server thread/INFO]: Steve joined the game".
# Requiring the name right after "]: " keeps chat lines ("]: <Steve> ...") from matching.
_PLAYER_EVENT = re.compile(r"\]: (\w{1,16}) (joined|left) the game$")


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
    playit_cancel: bool = False
    process: Any = None
    # True once a stop was asked for (button, restart, console "stop", shutdown); a non-zero exit
    # without it counts as a crash. crash_times holds monotonic timestamps of recent crashes.
    stop_requested: bool = False
    shutting_down: bool = False
    crash_times: list[float] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def track_player_event(self, line: str) -> None:
        """Update online_players from a server console line (join/leave messages)."""
        match = _PLAYER_EVENT.search(line)
        if not match:
            return
        name, event = match.groups()
        with self.lock:
            if event == "joined":
                self.online_players.add(name)
            else:
                self.online_players.discard(name)

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
                "install_status": self.install_status,
                "install_progress": self.install_progress,
                "install_message": self.install_message,
                "playit": {
                    "state": self.playit_state,
                    "address": self.playit_address,
                },
            }
