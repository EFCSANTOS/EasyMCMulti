"""Server identity: icon, MOTD and the All-properties editor (FEAT-14)."""
from __future__ import annotations

import os
import struct
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core import config_store, fsutil, server_process
from webapp import common, properties_meta
from webapp.services import Services, require_auth
from webapp.validation import validate_config_update

ICON_FILE = "server-icon.png"
ICON_MAX_BYTES = 64 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR chunk, or None if it isn't a PNG."""
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def register(app: FastAPI, s: Services) -> None:
    def server_dir() -> str:
        return common._server_dir_for_config(common._effective_config())

    def properties_payload(message: str = "") -> dict[str, Any]:
        config = common._effective_config()
        on_disk = server_process.read_properties(server_dir())
        extra = config.get("extra_properties") or {}
        rows = []
        for key in sorted(set(on_disk) | set(properties_meta.KNOWN) | set(extra) | properties_meta.MANAGED):
            managed = key in properties_meta.MANAGED or key in properties_meta.LOCKED
            rows.append({"key": key, "value": str(extra.get(key, on_disk.get(key, ""))), "managed": managed,
                         "custom": key in extra, **properties_meta.describe(key)})
        return {"properties": rows, "message": message}

    @app.get("/api/server/properties")
    def get_properties(_: str = Depends(require_auth)) -> dict[str, Any]:
        return properties_payload()

    @app.put("/api/server/properties")
    def save_properties(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        """{key: value} merged into extra_properties; null removes a key. Written on the next start."""
        config = common._effective_config()
        extra = dict(config.get("extra_properties") or {})
        for key, value in payload.items():
            if value is None:
                extra.pop(key, None)
            else:
                extra[key] = "true" if value is True else "false" if value is False else str(value)
        config.update(validate_config_update({"extra_properties": extra}, common._host_ram_mb()))
        config_store.save(config)
        note = " They apply when the server restarts." if s.server_running() else ""
        return properties_payload(f"Properties saved.{note}")

    # ---------- Server icon ----------
    @app.get("/api/server/icon")
    def get_icon(_: str = Depends(require_auth)) -> FileResponse:
        path = os.path.join(server_dir(), ICON_FILE)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="No server icon yet.")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/server/icon")
    def upload_icon(file: UploadFile = File(...), _: str = Depends(require_auth)) -> dict[str, Any]:
        """A 64×64 PNG (the dashboard resizes any picture in the browser before uploading)."""
        data = file.file.read(ICON_MAX_BYTES + 1)
        if len(data) > ICON_MAX_BYTES:
            raise HTTPException(status_code=400, detail="The icon must be smaller than 64 KB.")
        size = png_size(data)
        if size is None:
            raise HTTPException(status_code=400, detail="The icon must be a PNG image.")
        if size != (64, 64):
            raise HTTPException(status_code=400, detail=f"The icon must be 64×64 pixels (this one is {size[0]}×{size[1]}).")
        fsutil.write_bytes_atomic(os.path.join(server_dir(), ICON_FILE), data)
        return {"ok": True, "message": "Icon saved. It shows in the server list after the next restart."}

    @app.delete("/api/server/icon")
    def delete_icon(_: str = Depends(require_auth)) -> dict[str, Any]:
        path = os.path.join(server_dir(), ICON_FILE)
        if os.path.isfile(path):
            os.remove(path)
        return {"ok": True, "message": "Icon removed."}
