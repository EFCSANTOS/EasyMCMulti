"""World backups: create (manual, scheduled, safety), list, prune, restore (FEAT-01)."""
from __future__ import annotations

import os
import shutil
import threading
import time
import zipfile
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import backups
from webapp import common
from webapp.auth import User
from webapp.services import Services, require_auth, require_user

# The server's answer to "save-all flush" (1.13+: "Saved the game"; older: "Saved the world").
SAVED_LINE = r"Saved the (game|world)"


class BackupError(Exception):
    """A backup could not be made; the message is shown to the owner. `busy`: try again later (409)."""

    def __init__(self, message: str, busy: bool = False) -> None:
        super().__init__(message)
        self.busy = busy


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line
    notify = s.notify
    backup_lock = threading.Lock()

    def settings() -> dict[str, Any]:
        config = common._effective_config()
        return {
            "interval_hours": int(config.get("backup_interval_hours", 6)),
            "keep_recent": int(config.get("backup_keep_recent", 8)),
            "keep_daily": int(config.get("backup_keep_daily", 7)),
            "prune_manual": bool(config.get("backup_prune_manual", False)),
        }

    def quiet_zip(sdir: str, folder: str, label: str) -> str:
        """Zip while the server runs: stop autosave, flush, wait for "Saved the game", zip, resume."""
        process = runtime.process
        process.send("save-off")
        try:
            if not s.wait_for_console(SAVED_LINE, lambda: process.send("save-all flush"), common.BACKUP_SAVE_TIMEOUT_SEC):
                append_console_line("[backup] the server did not confirm the save in time; backing up anyway")
            return backups.create(sdir, folder, label)
        finally:
            process.send("save-on")

    def create_backup(label: str, folder: str | None = None, wait: bool = False) -> dict[str, Any]:
        """One backup of `folder` (default: the active world), then pruning. Raises BackupError."""
        if not backup_lock.acquire(blocking=wait):
            raise BackupError("A backup is already running.", busy=True)
        try:
            config = common._effective_config()
            sdir = common._server_dir_for_config(config)
            folder = folder or common._active_world(config)
            if not os.path.isdir(os.path.join(sdir, folder)):
                return {"ok": False, "message": f"The world '{folder}' has not been generated yet; start the server once."}
            previous = next((b for b in backups.list_backups(sdir) if b["folder"] == folder), None)
            free = shutil.disk_usage(sdir).free
            if previous and free < 2 * previous["size"]:
                raise BackupError(f"Not enough free space for a backup ({free // 2**20} MB free, the last backup "
                                  f"was {previous['size'] // 2**20} MB). Delete old backups or free up storage.")
            active = folder == common._active_world(config)
            if active and s.server_running():
                archive = quiet_zip(sdir, folder, label)
            else:
                # Server stopped: hold the lifecycle lock so nobody starts it halfway through the zip.
                if not runtime.lifecycle.acquire(timeout=common.LIFECYCLE_WAIT_SEC if wait else 0):
                    raise BackupError("The server is busy starting or stopping; try again in a moment.", busy=True)
                try:
                    archive = backups.create(sdir, folder, label)
                finally:
                    runtime.lifecycle.release()
            plan = settings()
            pruned = backups.prune(sdir, plan["keep_recent"], plan["keep_daily"], plan["prune_manual"])
            runtime.backup_dirty = s.server_running()
            append_console_line(f"[backup] created {os.path.basename(archive)}"
                                + (f"; removed {len(pruned)} old backup(s)" if pruned else ""))
            return {"ok": True, "archive": archive, "pruned": pruned,
                    "message": f"Backup created: {os.path.basename(archive)}"}
        except OSError as exc:
            raise BackupError(f"Backup failed: {exc}") from exc
        finally:
            backup_lock.release()

    def report_failure(label: str, exc: Exception) -> None:
        append_console_line(f"[backup] {label} backup failed: {exc}")
        notify("backup", "Backup failed", f"The {label} backup could not be made: {exc}")

    def safety_backup(reason: str, folder: str | None = None) -> bool:
        """Backup labelled auto-pre-<reason> before something risky. False (and an alert) on failure."""
        try:
            return bool(create_backup(f"auto-pre-{reason}", folder, wait=True).get("ok"))
        except BackupError as exc:
            report_failure(f"pre-{reason}", exc)
            return False

    def backup_due(now: float) -> bool:
        plan = settings()
        if plan["interval_hours"] <= 0:
            return False
        if s.server_running():
            runtime.backup_dirty = True  # the world is changing
        if not runtime.backup_dirty:
            return False  # no point backing up an unchanged world
        config = common._effective_config()
        folder = common._active_world(config)
        latest = next((b for b in backups.list_backups(common._server_dir_for_config(config)) if b["folder"] == folder), None)
        return latest is None or now - latest["created"].timestamp() >= plan["interval_hours"] * 3600

    def backup_tick(now: float | None = None) -> None:
        """One backup check (webapp/scheduler.py calls it every SCHEDULER_TICK_SEC)."""
        if not backup_due(now or time.time()):
            return
        try:
            create_backup("auto", wait=True)
        except BackupError as exc:
            report_failure("scheduled", exc)

    s.safety_backup = safety_backup
    s.backup_tick = backup_tick

    @app.post("/api/server/backup")
    def backup_server(user: User = Depends(require_user)) -> dict[str, Any]:
        """Zip the active world (and its Paper dimension folders). Friends may do this too."""
        if not user.is_owner:
            append_console_line(f"[dashboard] {user} made a backup")
        try:
            return create_backup("manual")
        except BackupError as exc:
            report_failure("manual", exc)
            raise HTTPException(status_code=409 if exc.busy else 500, detail=str(exc)) from exc

    @app.get("/api/server/backups")
    def list_backups(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = common._effective_config()
        items = backups.list_backups(common._server_dir_for_config(config))
        plan = settings()
        return {
            "backups": [item["path"] for item in items],  # older clients: paths only
            "items": [{"name": item["name"], "path": item["path"], "folder": item["folder"], "label": item["label"],
                       "size": item["size"], "created": item["created"].isoformat(timespec="seconds"),
                       "auto": backups.is_auto(item["label"])} for item in items],
            "total_size": sum(item["size"] for item in items),
            "schedule": plan,
        }

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
