from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
from typing import Any


@dataclass
class RuntimeState:
    loader: str = "vanilla"
    version: str = "unknown"
    state: str = "stopped"
    operation: str = "idle"
    install_status: str = "ready"
    install_progress: float = 0.0
    install_message: str = ""
    players: int = 0
    lan_address: str = "unknown"
    public_address: str | None = None
    playit_state: str = "unknown"
    playit_address: str | None = None
    playit_agent: Any = None
    process: Any = None
    console_history: deque[str] = field(default_factory=lambda: deque(maxlen=2000))
    lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "loader": self.loader,
                "version": self.version,
                "players": self.players,
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
