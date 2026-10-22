"""Storage use and cleanup (FEAT-15)."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import storage
from webapp import common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    def active_dir() -> str:
        return common._server_dir_for_config(common._effective_config())

    @app.get("/api/storage")
    def storage_usage(fresh: bool = False, _: str = Depends(require_auth)) -> dict[str, Any]:
        return storage.usage(active_dir(), fresh=fresh)

    @app.post("/api/storage/delete")
    def storage_delete(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        if s.server_running():
            raise HTTPException(status_code=409, detail="Stop the server before cleaning up.")
        if not runtime.lifecycle.acquire(blocking=False):
            raise s.busy_error()
        try:
            name, freed = storage.delete(str(payload.get("id", "")), active_dir())
        except KeyError:
            raise HTTPException(status_code=404, detail="That item no longer exists; refresh the list.") from None
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not delete it: {exc}") from exc
        finally:
            runtime.lifecycle.release()
        append_console_line(f"[storage] deleted {name} ({freed // 2**20} MB)")
        amount = f"{freed / 2**30:.1f} GB" if freed >= 2**30 else f"{max(1, round(freed / 2**20))} MB"
        return {**storage.usage(active_dir(), fresh=True), "message": f"Deleted {name}: {amount} freed."}

    def storage_tick() -> None:
        """Alert once when the data drive runs low, again only after it recovered (scheduler tick)."""
        info = storage.disk()
        if info["low"] and not runtime.low_space_alerted:
            runtime.low_space_alerted = True
            free_gb = info["free"] / 2**30
            append_console_line(f"[storage] low on space: {free_gb:.1f} GB free")
            s.notify("backup", "Storage almost full",
                     f"Only {free_gb:.1f} GB is free on the device. A full disk can corrupt the world when it saves. "
                     "Open Backups → Storage to free space.", throttle=False)
        elif not info["low"]:
            runtime.low_space_alerted = False

    s.storage_tick = storage_tick
