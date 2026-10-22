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

    def status_payload(config: dict[str, Any]) -> dict[str, Any]:
        """The runtime snapshot plus values derived from the config, the network and PlayIt.

        A pure read (FIX-06): polling it never changes `runtime`, so behaviour doesn't depend on
        whether a dashboard tab is open. State that must be kept is stored where it changes
        (config save, server start, PlayIt start/stop).
        """
        payload = runtime.snapshot()
        public_address = netinfo.public_ip()
        playit_cfg = playit.load_config()
        payload.update(
            loader=str(config.get("loader", runtime.loader)),
            version=str(config.get("version", runtime.version)),
            lan_address=netinfo.lan_ip(),
            public_address=public_address,
            playit={"state": common._playit_state(runtime, playit_cfg),
                    "address": playit_cfg.get("address") or public_address},
        )
        return payload

    @app.get("/api/status")
    def status(_: str = Depends(require_auth)) -> dict[str, Any]:
        return status_payload(common._effective_config())

    @app.get("/api/console")
    def console_history(since: int | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        """Console history; with ?since=<n> only the lines after sequence number n (FEAT-11)."""
        manager = app.state.console_manager
        lines = list(manager.history) if since is None else manager.lines_since(since)
        return {"lines": lines, "last": manager.last_seq}

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
