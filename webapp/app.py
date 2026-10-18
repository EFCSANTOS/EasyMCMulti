from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import config_store, downloaders
from core.server_process import ServerProcess
from webapp.auth import validate_auth_settings, verify_basic_auth_header, verify_credentials
from webapp.state import RuntimeState
from webapp.websocket_manager import WebSocketManager

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
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


def create_app() -> FastAPI:
    validate_auth_settings()
    app = FastAPI(title="MZ Minecraft Server Launcher", version="0.1.0")
    runtime = RuntimeState()
    app.state.runtime = runtime
    app.state.console_manager = WebSocketManager()

    def append_console_line(line: str) -> None:
        app.state.console_manager.publish(line)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"message": "MZ Minecraft Server web UI is starting."}

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "mz-server-webapp"}

    @app.get("/api/status")
    def status(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = config_store.load()
        runtime.loader = config.get("loader", runtime.loader)
        runtime.version = config.get("version", runtime.version)
        return runtime.snapshot()

    @app.get("/api/config")
    def get_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return config_store.load()

    @app.put("/api/config")
    def save_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config_store.save(payload)
        if "loader" in payload:
            runtime.loader = payload["loader"]
        if "version" in payload:
            runtime.version = payload["version"]
        return payload

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

    @app.post("/api/server/start")
    def start_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}

        runtime.state = "starting"
        runtime.operation = "starting"

        def on_exit(code: int) -> None:
            runtime.state = "stopped"
            runtime.operation = "idle"
            runtime.process = None
            append_console_line(f"[server] exited with code {code}")

        demo_script = (
            "import sys, time; "
            "print('Demo server booting...', flush=True); "
            "for i in range(3): "
            "print(f'demo-line-{i}', flush=True); time.sleep(0.2)"
        )
        runtime.process = ServerProcess(
            [sys.executable, "-c", demo_script],
            cwd=os.getcwd(),
            on_output=append_console_line,
            on_exit=on_exit,
        )
        runtime.process.start()
        runtime.state = "running"
        runtime.operation = "idle"
        append_console_line("[server] started")
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
