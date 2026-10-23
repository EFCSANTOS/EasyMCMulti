"""Bedrock crossplay switch (FEAT-18): Geyser + Floodgate and a PlayIt Bedrock tunnel."""
from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import config_store, geyser, playit
from webapp import common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    def info(message: str = "") -> dict[str, Any]:
        config = common._effective_config()
        loader = str(config.get("loader", "vanilla"))
        sdir = common._server_dir_for_config(config)
        return {
            "supported": geyser.supported(loader),
            "loader": loader,
            "enabled": bool(config.get("bedrock_enabled")),
            "busy": runtime.bedrock_busy,
            "port": int(config.get("bedrock_port", geyser.DEFAULT_PORT)),
            "address": str(playit.load_config().get("bedrock_address") or "") if config.get("bedrock_enabled") else "",
            # Geyser writes its config on the first start; the next start applies our port/auth settings.
            "configured": os.path.isfile(geyser.config_path(sdir, loader)) if geyser.supported(loader) else False,
            "error": runtime.bedrock_error,
            "message": message,
        }

    def enable_in_background(config: dict[str, Any]) -> None:
        loader, version = str(config["loader"]), str(config["version"])
        sdir = common._server_dir_for_config(config)
        try:
            geyser.install(loader, version, sdir, log=lambda line: append_console_line(f"[bedrock] {line}"))
            config_store.save({**common._effective_config(), "bedrock_enabled": True})
            runtime.bedrock_error = ""
            append_console_line("[bedrock] Geyser and Floodgate installed; restart the server to load them")
            secret = playit.load_config().get("secret")
            if secret and runtime.playit_agent and runtime.playit_agent.is_running():
                s.ensure_bedrock_tunnel(secret, common._effective_config())
        except Exception as exc:
            runtime.bedrock_error = f"Could not set up Bedrock crossplay: {exc}"
            append_console_line(f"[bedrock] {runtime.bedrock_error}")
        finally:
            runtime.bedrock_busy = False

    @app.get("/api/bedrock")
    def bedrock_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        return info()

    @app.post("/api/bedrock")
    def bedrock_toggle(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config = common._effective_config()
        loader = str(config.get("loader", "vanilla"))
        if runtime.bedrock_busy:
            raise HTTPException(status_code=409, detail="Bedrock setup is still running.")
        if payload.get("enabled"):
            if not geyser.supported(loader):
                raise HTTPException(status_code=400, detail="Bedrock crossplay needs a Paper, Fabric or NeoForge server "
                                                            "(Geyser isn't available for vanilla or Forge).")
            runtime.bedrock_busy = True
            runtime.bedrock_error = ""
            threading.Thread(target=enable_in_background, args=(config,), name="bedrock", daemon=True).start()
            return info("Installing Geyser and Floodgate…")
        removed = geyser.uninstall(loader, common._server_dir_for_config(config)) if geyser.supported(loader) else []
        config["bedrock_enabled"] = False
        config_store.save(config)
        note = " Restart the server to unload them." if removed and s.server_running() else ""
        return info(f"Bedrock crossplay is off; removed {len(removed)} plugin(s).{note}")
