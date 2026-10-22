"""Crash diagnosis and the server log viewer (FEAT-08)."""
from __future__ import annotations

import gzip
import os
import re
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from core import diagnose
from webapp import common
from webapp.services import Services, require_auth

# Console lines scanned after a crash, and lines shown by the log viewer.
DIAGNOSIS_LINES = 300
LOG_VIEW_LINES = 2000
LOG_FOLDERS = ("logs", "crash-reports")
_LOG_NAME = re.compile(r"^[\w.-]+\.(?:log|txt|log\.gz)$")


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    def server_dir() -> str:
        return common._server_dir_for_config(common._effective_config())

    def newest_crash_report(since: float) -> str:
        folder = os.path.join(server_dir(), "crash-reports")
        if not os.path.isdir(folder):
            return ""
        reports = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".txt")]
        reports = [p for p in reports if os.path.getmtime(p) >= since - 5]
        if not reports:
            return ""
        with open(max(reports, key=os.path.getmtime), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(200_000)

    def run_diagnosis() -> list[dict[str, Any]]:
        """Diagnose the last run from the console history and a crash report written since the start."""
        lines = list(app.state.console_manager.history)[-DIAGNOSIS_LINES:]
        items = diagnose.diagnose(lines, newest_crash_report(runtime.last_start_at))
        runtime.diagnosis = items
        runtime.diagnosis_at = time.time()
        for item in items:
            append_console_line(f"[diagnosis] {item['title']}: {item['message']}")
        return items

    def clear_diagnosis() -> None:
        runtime.diagnosis = []
        runtime.diagnosis_at = 0.0

    s.run_diagnosis = run_diagnosis
    s.clear_diagnosis = clear_diagnosis

    @app.get("/api/server/diagnosis")
    def get_diagnosis(_: str = Depends(require_auth)) -> dict[str, Any]:
        return {"items": runtime.diagnosis, "at": runtime.diagnosis_at}

    def java_pid_on_port(port: int) -> tuple[int, str] | None:
        if common.psutil is None:
            return None
        try:
            for conn in common.psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN" and conn.pid:
                    name = common.psutil.Process(conn.pid).name()
                    return conn.pid, name
        except (common.psutil.Error, OSError):
            return None
        return None

    @app.post("/api/server/diagnosis/fix")
    def fix_diagnosis(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        kind = str(payload.get("kind", ""))
        if s.server_running():
            raise HTTPException(status_code=409, detail="Stop the server first.")
        if kind == "accept_eula":
            common.accept_eula(server_dir())
            message = "EULA accepted. Start the server again."
        elif kind == "kill_process":
            try:
                port = int(payload.get("port"))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="A port is required.") from None
            found = java_pid_on_port(port)
            if found is None:
                raise HTTPException(status_code=404, detail=f"No program found listening on port {port} "
                                                            "(it may have closed already). Try starting again.")
            pid, name = found
            if "java" not in name.lower():  # never kill something that isn't a Minecraft/Java server
                raise HTTPException(status_code=409, detail=f"Port {port} is used by {name} (PID {pid}), not a "
                                                            "Minecraft server. Close it yourself or change the port.")
            common.psutil.Process(pid).terminate()
            append_console_line(f"[diagnosis] stopped {name} (PID {pid}) that was holding port {port}")
            message = f"Stopped the old server (PID {pid}). Start the server again."
        else:
            raise HTTPException(status_code=400, detail=f"Nothing to fix automatically for: {kind or '(none)'}")
        clear_diagnosis()
        return {"ok": True, "message": message}

    # ---------- Log viewer ----------
    def log_path(folder: str, name: str) -> str:
        if folder not in LOG_FOLDERS or not _LOG_NAME.match(name):
            raise HTTPException(status_code=400, detail="Unknown log file.")
        path = os.path.join(server_dir(), folder, name)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Log file not found.")
        return path

    @app.get("/api/server/logs")
    def list_logs(_: str = Depends(require_auth)) -> dict[str, Any]:
        files = []
        for folder in LOG_FOLDERS:
            base = os.path.join(server_dir(), folder)
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if _LOG_NAME.match(name) and os.path.isfile(path):
                    files.append({"folder": folder, "name": name, "size": os.path.getsize(path),
                                  "modified": os.path.getmtime(path)})
        files.sort(key=lambda item: item["modified"], reverse=True)
        return {"files": files}

    @app.get("/api/server/logs/{folder}/{name}")
    def read_log(folder: str, name: str, download: bool = False, _: str = Depends(require_auth)) -> Any:
        path = log_path(folder, name)
        if download:
            return FileResponse(path, filename=name)
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        return {"name": name, "folder": folder, "lines": lines[-LOG_VIEW_LINES:],
                "truncated": len(lines) > LOG_VIEW_LINES}
