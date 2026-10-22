"""Friend invite and the public status page (FEAT-03).

`/s/<token>` is the only page friends can open without signing in. It is off by default, the token
is random and can be regenerated, and it shows only: server name, online/offline, players, join
address, version and whether mods are needed. A wrong token is a plain 404 (nothing to probe).
"""
from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from core import config_store, netinfo, playit
from webapp import common
from webapp.services import Services, require_auth

LOADER_LABELS = {"vanilla": "Vanilla", "paper": "Paper", "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge"}
# Loaders whose mods players must install too (Paper plugins are server-side only).
CLIENT_MOD_LOADERS = ("fabric", "forge", "neoforge")
DEFAULT_MC_PORT = 25565
_TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "status.html")


def _with_port(host: str, port: int) -> str:
    return host if port == DEFAULT_MC_PORT else f"{host}:{port}"


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime

    def join_address(config: dict[str, Any]) -> str:
        """PlayIt address if set up, else the public IP (works only with the port forwarded)."""
        address = str(playit.load_config().get("address") or "").strip()
        if address:
            return address
        public = netinfo.public_ip()
        return _with_port(public, int(config.get("server_port", DEFAULT_MC_PORT))) if public != "unavailable" else ""

    def public_status(config: dict[str, Any]) -> dict[str, Any]:
        """Everything the public page shows, and nothing else."""
        loader = str(config.get("loader", "vanilla"))
        running = s.server_running()
        snapshot = runtime.snapshot()
        data: dict[str, Any] = {
            "name": str(config.get("motd") or "Minecraft server"),
            "online": running,
            "state": snapshot["state"],
            "players_online": snapshot["players"] if running else 0,
            "players_max": int(config.get("max_players", 10)),
            "address": join_address(config),
            "version": str(config.get("version", "")),
            "loader": LOADER_LABELS.get(loader, loader),
            "mods_required": s.modpack_available(),
            # Relative to the status page; FEAT-04 builds the pack on first download.
            "modpack_url": "modpack.mrpack" if s.modpack_available() else "",
        }
        if config.get("status_page_show_names", True):
            data["player_names"] = snapshot["player_names"] if running else []
        if config.get("status_page_show_lan"):
            data["lan_address"] = _with_port(netinfo.lan_ip(), int(config.get("server_port", DEFAULT_MC_PORT)))
        return data

    def dashboard_base() -> str:
        return runtime.web_tunnel_url or f"http://{netinfo.lan_ip()}:{common.WEB_PORT}"

    def invite_text(config: dict[str, Any], page_url: str) -> str:
        status = public_status(config)
        lines = ["Join our Minecraft server!"]
        lines.append(f"Address: {status['address'] or '(set up PlayIt on the Remote access page)'}")
        version = f"Version: {status['loader']} {status['version']}"
        if status["mods_required"]:
            version += (" (mods required: get the mod pack from the status page)" if page_url
                        else " (mods required: ask me for the mod pack)")
        elif str(config.get("loader", "vanilla")) not in CLIENT_MOD_LOADERS:
            version += " (no mods needed)"
        lines.append(version)
        if page_url:
            lines.append(f"Status page: {page_url}")
        return "\n".join(lines)

    def owner_info(message: str = "") -> dict[str, Any]:
        config = common._effective_config()
        token = str(config.get("status_page_token") or "")
        enabled = bool(config.get("status_page_enabled")) and bool(token)
        url = f"{dashboard_base()}/s/{token}" if enabled else ""
        return {
            "enabled": enabled,
            "show_names": bool(config.get("status_page_show_names", True)),
            "show_lan": bool(config.get("status_page_show_lan")),
            "url": url,
            "url_is_lan_only": enabled and not runtime.web_tunnel_url,
            "invite": invite_text(config, url),
            "message": message,
        }

    def checked_token(token: str) -> dict[str, Any]:
        config = common._effective_config()
        expected = str(config.get("status_page_token") or "")
        if not (config.get("status_page_enabled") and expected and secrets.compare_digest(token, expected)):
            raise HTTPException(status_code=404, detail="Not found.")
        return config

    @app.get("/api/status-page")
    def status_page_settings(_: str = Depends(require_auth)) -> dict[str, Any]:
        return owner_info()

    @app.post("/api/status-page")
    def change_status_page(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        action = str(payload.get("action", "")).lower()
        config = common._effective_config()
        if action == "enable":
            config["status_page_enabled"] = True
            config["status_page_token"] = config.get("status_page_token") or secrets.token_urlsafe(16)
            message = "Status page on. Share the link with your friends."
        elif action == "disable":
            config["status_page_enabled"] = False
            message = "Status page off; the link no longer works."
        elif action == "regenerate":
            config["status_page_token"] = secrets.token_urlsafe(16)
            message = "New link created; the old one no longer works."
        elif action == "settings":
            for key, field in (("show_names", "status_page_show_names"), ("show_lan", "status_page_show_lan")):
                if key in payload:
                    config[field] = bool(payload[key])
            message = "Status page settings saved."
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action or '(none)'}")
        config_store.save(config)
        return owner_info(message)

    @app.get("/s/{token}")
    def public_status_page(token: str) -> HTMLResponse:
        checked_token(token)
        with open(_TEMPLATE, "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read(), headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    @app.get("/s/{token}/status.json")
    def public_status_json(token: str) -> dict[str, Any]:
        return public_status(checked_token(token))

    @app.get("/s/{token}/modpack.mrpack")
    def public_modpack(token: str) -> FileResponse:
        checked_token(token)
        try:
            path, summary = s.ensure_modpack()
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found.") from None
        return FileResponse(path, media_type="application/x-modrinth-modpack+zip", filename=summary["filename"],
                            headers={"Cache-Control": "no-store"})
