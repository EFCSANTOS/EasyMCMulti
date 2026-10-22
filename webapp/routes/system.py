"""Status, metrics, console history, config and versions."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import config_store, downloaders, netinfo, playit
from webapp import common
from webapp.services import Services, require_auth
from webapp.validation import validate_config_update


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime

    @app.get("/api/system/metrics")
    @app.get("/api/system")
    @app.get("/api/stats")
    @app.get("/api/metrics")
    @app.get("/metrics")
    def system_metrics(_: str = Depends(require_auth)) -> dict[str, Any]:
        return common._system_metrics_snapshot()

    @app.get("/api/status")
    def status(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = common._effective_config()
        runtime.loader = str(config.get("loader", runtime.loader))
        runtime.version = str(config.get("version", runtime.version))
        runtime.lan_address = netinfo.lan_ip()
        runtime.public_address = netinfo.public_ip()
        playit_cfg = playit.load_config()
        runtime.playit_state = common._playit_state(runtime, playit_cfg)
        runtime.playit_address = playit_cfg.get("address") or runtime.public_address
        return runtime.snapshot()

    @app.get("/api/console")
    def console_history(_: str = Depends(require_auth)) -> dict[str, Any]:
        return {"lines": list(app.state.console_manager.history)}

    @app.get("/api/config")
    def get_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return common._effective_config()

    @app.put("/api/config")
    def save_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config = common._effective_config()
        config.update(validate_config_update(payload, common._host_ram_mb()))
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
        if loader not in common.KNOWN_LOADERS:
            raise HTTPException(status_code=400, detail=f"Unsupported loader: {loader}")

        try:
            versions = downloaders.list_versions(loader, include_snapshots=False)
        except Exception:
            versions = common.FALLBACK_VERSIONS.get(loader, [])

        if not versions:
            raise HTTPException(status_code=502, detail=f"No versions available for loader: {loader}")

        return {"loader": loader, "versions": versions}
