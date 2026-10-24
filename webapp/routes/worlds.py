"""Worlds: list, create, upload, switch, rename, delete, download, carry player progress over."""
from __future__ import annotations

import os
import time
import zipfile
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from core import backups, config_store, paths, player_transfer, worlds
from webapp import common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    def worlds_payload(message: str = "") -> dict[str, Any]:
        config = common._effective_config()
        active = common._active_world(config)
        return {
            "worlds": worlds.list_worlds(common._server_dir_for_config(config), active),
            "active": active,
            "server_running": s.server_running(),
            "message": message,
        }

    def checked_world(folder: str) -> str:
        if not worlds.valid_folder(folder):
            raise HTTPException(status_code=400, detail="Invalid world.")
        return common._server_dir_for_config(common._effective_config())

    @app.get("/api/worlds")
    def list_worlds(_: str = Depends(require_auth)) -> dict[str, Any]:
        return worlds_payload()

    @app.post("/api/worlds")
    def create_world(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Give the new world a name.")
        folder = worlds.create(common._server_dir_for_config(common._effective_config()), name, str(payload.get("seed", "")).strip())
        return worlds_payload(f"Created '{name}'. Switch to it to generate it.") | {"folder": folder}

    @app.post("/api/worlds/upload")
    def upload_world(file: UploadFile = File(...), name: str = Form(default=""), _: str = Depends(require_auth)) -> dict[str, Any]:
        filename = os.path.basename(file.filename or "world.zip")
        if not filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="Upload the world as a .zip file.")
        sdir = common._server_dir_for_config(common._effective_config())
        paths.ensure_dirs()
        temp_path = os.path.join(paths.DATA_DIR, f"upload-{int(time.time() * 1000)}.zip")
        try:
            with open(temp_path, "wb") as handle:
                while chunk := file.file.read(1 << 20):
                    handle.write(chunk)
            display_name = name.strip() or os.path.splitext(filename)[0]
            try:
                folder = worlds.import_zip(sdir, temp_path, display_name)
            except (zipfile.BadZipFile, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc) or "Not a valid zip file.") from exc
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return worlds_payload(f"Imported '{display_name}'.") | {"folder": folder}

    @app.post("/api/worlds/{folder}/activate")
    def activate_world(folder: str, response: Response, _: str = Depends(require_auth)) -> dict[str, Any]:
        sdir = checked_world(folder)
        known = {w["folder"]: w for w in worlds.list_worlds(sdir, common._active_world(common._effective_config()))}
        if folder not in known:
            raise HTTPException(status_code=404, detail="World not found.")
        if known[folder]["active"]:
            return worlds_payload("That world is already active.")
        name = known[folder]["name"]

        def select_world() -> None:
            config = common._effective_config()
            config["level_name"] = folder
            if not known[folder]["exists"]:
                config["level_seed"] = known[folder].get("seed", "")  # only used when the world is generated
            config_store.save(config)

        if s.server_running():
            def switch_and_restart() -> str:
                s.stop_and_wait()
                select_world()
                s.launch_server()
                return f"Switched to '{name}' and restarted the server."

            s.start_lifecycle_job("switching world", switch_and_restart, "World switch failed", state="stopping")
            response.status_code = 202
            return worlds_payload(f"Switching to '{name}'; the server restarts in the background.")
        if not runtime.lifecycle.acquire(blocking=False):
            raise s.busy_error()
        try:
            select_world()
        finally:
            runtime.lifecycle.release()
        return worlds_payload(f"Switched to '{name}'. It loads the next time the server starts.")

    @app.post("/api/worlds/{folder}/rename")
    def rename_world(folder: str, payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        sdir = checked_world(folder)
        try:
            worlds.rename(sdir, folder, str(payload.get("name", "")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return worlds_payload("World renamed.")

    @app.delete("/api/worlds/{folder}")
    def delete_world(folder: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        sdir = checked_world(folder)
        if folder == common._active_world(common._effective_config()):
            raise HTTPException(status_code=409, detail="Switch to another world before deleting this one.")
        if os.path.isdir(os.path.join(sdir, folder)) and not s.safety_backup("delete", folder):
            raise HTTPException(status_code=500, detail="The safety backup failed, so the world was not deleted. "
                                                        "See the console for the reason.")
        worlds.delete(sdir, folder)
        return worlds_payload("World deleted.")

    @app.get("/api/worlds/{folder}/download")
    def download_world(folder: str, _: str = Depends(require_auth)) -> FileResponse:
        sdir = checked_world(folder)
        if s.server_running() and folder == common._active_world(common._effective_config()):
            runtime.process.send("save-all flush")
            time.sleep(2)
        paths.ensure_dirs()
        archive = os.path.join(paths.DATA_DIR, f"export-{folder}-{int(time.time())}.zip")
        try:
            worlds.export_zip(sdir, folder, archive)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(archive, media_type="application/zip", filename=f"{folder}.zip",
                            background=BackgroundTask(os.remove, archive))

    @app.get("/api/worlds/{folder}/players")
    def world_players(folder: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        sdir = checked_world(folder)
        try:
            return {"players": player_transfer.list_players(sdir, folder),
                    "mod_data": player_transfer.mod_data(sdir, folder)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/worlds/{folder}/transfer")
    def transfer_players(folder: str, payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        """Copy player progress from payload["source"] into this world (players and mod data chosen in the payload)."""
        sdir = checked_world(folder)
        source = str(payload.get("source", ""))
        if not worlds.valid_folder(source):
            raise HTTPException(status_code=400, detail="Choose the world to copy players from.")
        chosen = payload.get("players")
        if chosen is not None and not (isinstance(chosen, list) and all(isinstance(u, str) for u in chosen)):
            raise HTTPException(status_code=400, detail="Invalid player list.")
        parts = payload.get("parts", list(player_transfer.PARTS))
        extras = payload.get("mod_data", [])
        if not isinstance(parts, list) or not isinstance(extras, list):
            raise HTTPException(status_code=400, detail="Invalid request.")
        if s.server_running():
            raise HTTPException(status_code=409, detail="Stop the server before copying player data: it rewrites "
                                                        "player files while running.")
        if not runtime.lifecycle.acquire(blocking=False):
            raise s.busy_error()
        try:
            plan = player_transfer.prepare(sdir, source, folder, chosen, [str(p) for p in parts],
                                           bool(payload.get("to_spawn", True)), [str(e) for e in extras])
            # Holding the lifecycle lock: back up with core.backups directly (the FEAT-01 helper takes the lock).
            append_console_line(f"[transfer] backing up '{folder}' first")
            backups.create(sdir, folder, "auto-pre-transfer")
            result = player_transfer.apply(plan, log=lambda line: append_console_line(f"[transfer] {line}"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Copying player data failed: {exc}") from exc
        finally:
            runtime.lifecycle.release()
        message = f"Copied progress for {result['players']} player(s)"
        if result["extras"]:
            message += f" and {len(result['extras'])} mod data item(s)"
        message += ". A backup of the world was made first."
        return worlds_payload(message) | {"transfer": result}
