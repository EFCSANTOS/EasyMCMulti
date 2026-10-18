from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import config_store, downloaders
from webapp.auth import validate_auth_settings, verify_credentials
from webapp.state import RuntimeState

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

    return app


app = create_app()
