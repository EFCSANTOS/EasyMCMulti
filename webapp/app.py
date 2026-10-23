from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from webapp import background, scheduler
from webapp.auth import authenticate, validate_auth_settings
from webapp.routes import (
    backups, bedrock, diagnosis, health, identity, migrate, mods, pages, players, public, remote, server, storage,
    system, updates, users, worlds,
)
from webapp.services import create_services


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # autostart/shutdown are set at the end of create_app.
    app.state.autostart()
    yield
    await asyncio.to_thread(app.state.shutdown)


def create_app() -> FastAPI:
    validate_auth_settings()
    app = FastAPI(title="EZ Minecraft Server Launcher", version="0.1.0", lifespan=_lifespan)
    services = create_services(app)
    app.state.services = services
    for module in (pages, system, server, diagnosis, backups, mods, worlds, players, public, identity, storage, migrate,
                   users, remote, bedrock, health, updates, scheduler, background):
        module.register(app, services)

    app.state.autostart = services.autostart
    app.state.shutdown = services.shutdown
    app.state.restart_after_crash = services.restart_after_crash
    app.state.notify = services.notify

    @app.websocket("/ws/console")
    async def console_websocket(websocket: WebSocket) -> None:
        state = app.state
        try:
            user = authenticate(websocket, state.sessions, state.login_limiter, state.on_lockout)
            if not user.is_owner:  # the console is owner-only (it shows player IPs and commands)
                raise HTTPException(status_code=403)
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
