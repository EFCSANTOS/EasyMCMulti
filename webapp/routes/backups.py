"""World backups: create, list, restore."""
from __future__ import annotations

import os
import time
import zipfile
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import worlds
from webapp import common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime

    @app.post("/api/server/backup")
    def backup_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        """Zip the active world (and its Paper dimension folders)."""
        config = common._effective_config()
        target_dir = common._server_dir_for_config(config)
        folder = common._active_world(config)
        if not os.path.isdir(os.path.join(target_dir, folder)):
            return {"ok": False, "message": f"The active world '{folder}' has not been generated yet; start the server once."}
        if runtime.process is not None and runtime.process.is_running():
            runtime.process.send("save-all flush")  # write pending chunks before copying
            time.sleep(2)
        archive = os.path.join(target_dir, f"world-backup-{folder}-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        worlds.export_zip(target_dir, folder, archive)
        return {"ok": True, "archive": archive, "message": f"Backup created: {os.path.basename(archive)}"}

    @app.get("/api/server/backups")
    def list_backups(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = common._effective_config()
        target_dir = common._server_dir_for_config(config)
        backups = []
        if os.path.isdir(target_dir):
            for entry in sorted(os.listdir(target_dir), reverse=True):
                if common._is_backup_name(entry):
                    backups.append(os.path.join(target_dir, entry))
        return {"backups": backups}

    @app.post("/api/server/backups/restore")
    def restore_backup(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        if not runtime.lifecycle.acquire(blocking=False):
            raise s.busy_error()
        try:
            return restore_backup_locked(payload)
        finally:
            runtime.lifecycle.release()

    def restore_backup_locked(payload: dict[str, Any]) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Stop the server before restoring a backup.")
        # Only this server's own backups are accepted; a full path from the list is reduced to its name.
        name = os.path.basename(str(payload.get("backup_path", "") or payload.get("name", "")).strip())
        target_dir = common._server_dir_for_config(common._effective_config())
        backup_path = os.path.join(target_dir, name)
        if not (common._is_backup_name(name) and os.path.isfile(backup_path)):
            raise HTTPException(status_code=400, detail="A valid backup from the backup list is required.")

        with zipfile.ZipFile(backup_path, "r") as zf:
            members = zf.namelist()
            unsafe = [m for m in members if not common._is_safe_world_member(m)]
            if unsafe:
                raise HTTPException(status_code=400, detail=f"Backup contains files outside a world folder: {unsafe[0]}")
            # Move the current copy of each world folder in the backup aside instead of overwriting it.
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for root in sorted({m.replace("\\", "/").split("/")[0] for m in members}):
                current = os.path.join(target_dir, root)
                if os.path.isdir(current):
                    os.rename(current, os.path.join(target_dir, f"{root}-pre-restore-{stamp}"))
            zf.extractall(target_dir, members=members)
        return {"ok": True, "message": f"Backup restored from {name}"}

    @app.post("/api/backups/restore")
    def legacy_restore_backup(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        return restore_backup(payload, _)

    @app.get("/api/backups")
    def legacy_list_backups(_: str = Depends(require_auth)) -> dict[str, Any]:
        return list_backups(_)
