from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from core import config_store, downloaders

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
FALLBACK_VERSIONS = {
    "vanilla": ["1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.20.4"],
    "paper": ["1.21.7", "1.21.6", "1.21.4", "1.21.1", "1.20.4"],
    "fabric": ["1.21.7", "1.21.6", "1.21.4", "1.20.4"],
    "forge": ["1.20.1", "1.19.2", "1.16.5"],
    "neoforge": ["1.21.7", "1.21.6", "1.21.1", "1.20.1"],
}


def create_app() -> FastAPI:
    app = FastAPI(title="MZ Minecraft Server Launcher", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "mz-server-webapp"}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        config = config_store.load()
        loader = config.get("loader", "vanilla")
        version = config.get("version", "unknown")
        return {
            "state": "stopped",
            "loader": loader,
            "version": version,
            "players": 0,
            "lan_address": "unknown",
            "public_address": None,
            "operation": "idle",
            "playit": {"state": "unknown", "address": None},
        }

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return config_store.load()

    @app.put("/api/config")
    def save_config(payload: dict[str, Any]) -> dict[str, Any]:
        config_store.save(payload)
        return payload

    @app.get("/api/versions/{loader}")
    def versions(loader: str) -> dict[str, Any]:
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
