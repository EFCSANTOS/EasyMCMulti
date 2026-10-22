"""Installed mods/plugins and the Modrinth browser."""
from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile

from core import modrinth
from webapp import common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    @app.get("/api/server/mods")
    def list_mods(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = common._effective_config()
        target_dir = common._server_dir_for_config(config)
        mods_dir = os.path.join(target_dir, "mods")
        plugins_dir = os.path.join(target_dir, "plugins")
        items = []
        project_ids = []
        for base_dir in (mods_dir, plugins_dir):
            if not os.path.isdir(base_dir):
                continue
            by_file = {info["filename"]: (pid, info) for pid, info in modrinth.installed_projects(base_dir).items()}
            for name in sorted(os.listdir(base_dir)):
                if name.lower().endswith(".jar"):
                    pid, info = by_file.get(name, (None, {}))
                    if pid:
                        project_ids.append(pid)
                    items.append({"name": name, "path": os.path.join(base_dir, name),
                                  "folder": os.path.basename(base_dir), "title": info.get("title", ""),
                                  "project_id": pid, "version": info.get("version", "")})
        return {"mods": items, "project_ids": project_ids}

    @app.delete("/api/server/mods/{name}")
    def remove_mod(name: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Stop the server before removing mods (they are in use).")
        name = os.path.basename(name)
        target_dir = common._server_dir_for_config(common._effective_config())
        for folder in ("mods", "plugins"):
            path = os.path.join(target_dir, folder, name)
            if name.lower().endswith(".jar") and os.path.isfile(path):
                os.remove(path)
                modrinth.forget_file(os.path.dirname(path), name)
                return {"ok": True, "message": f"Removed {name}."}
        raise HTTPException(status_code=404, detail=f"{name} is not installed.")

    @app.post("/api/server/mods/upload")
    def upload_mods(files: list[UploadFile] = File(default=[]), _: str = Depends(require_auth)) -> dict[str, Any]:
        if not files:
            raise HTTPException(status_code=400, detail="No mod files were uploaded.")
        mod_target = common._content_dir_for_config(common._effective_config())
        saved = []
        for upload in files:
            name = os.path.basename(upload.filename or "")
            if not name.lower().endswith(".jar"):
                continue
            dest = os.path.join(mod_target, name)
            with open(dest, "wb") as handle:
                while True:
                    chunk = upload.file.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
            saved.append(dest)
        if not saved:
            raise HTTPException(status_code=400, detail="Only .jar files can be uploaded.")
        return {"ok": True, "saved": saved, "message": f"Uploaded {len(saved)} file(s)."}

    @app.get("/api/mods")
    def legacy_list_mods(_: str = Depends(require_auth)) -> dict[str, Any]:
        return list_mods(_)

    def modrinth_context() -> tuple[str, str]:
        config = common._effective_config()
        loader = str(config.get("loader", "vanilla"))
        if loader == "vanilla":
            raise HTTPException(status_code=400, detail="Vanilla servers can't use mods or plugins. Choose Paper, "
                                                        "Fabric, Forge or NeoForge in Server setup.")
        return loader, str(config.get("version", "1.21.7"))

    def upstream_error(exc: Exception) -> HTTPException:
        return HTTPException(status_code=502, detail=f"Modrinth is not reachable right now: {exc}")

    @app.get("/api/modrinth/search")
    def modrinth_search(query: str = "", sort: str = "relevance", category: str = "", offset: int = 0,
                        _: str = Depends(require_auth)) -> dict[str, Any]:
        """Search, or browse the most popular projects when the query is empty."""
        loader, version = modrinth_context()
        try:
            result = modrinth.search(query.strip(), loader, version, limit=common.MODRINTH_PAGE_SIZE,
                                     offset=max(0, offset), sort=sort, category=category.strip())
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc
        return {**result, "items": result["hits"], "loader": loader, "version": version}

    @app.get("/api/modrinth/categories")
    def modrinth_categories(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            return {"categories": modrinth.categories()}
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc

    @app.get("/api/modrinth/project/{project_ref}")
    def modrinth_project(project_ref: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        loader, version = modrinth_context()
        try:
            return modrinth.project(project_ref, loader, version)
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc

    @app.post("/api/modrinth/install")
    def modrinth_install(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        project_ref = str(payload.get("slug") or payload.get("project_id") or payload.get("id") or "").strip()
        if not project_ref:
            raise HTTPException(status_code=400, detail="A Modrinth project slug or ID is required.")
        config = common._effective_config()
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_folder = common._content_dir_for_config(config)
        before = set(modrinth.installed_projects(target_folder))
        try:
            installed = modrinth.install(project_ref, loader, version, target_folder, log=append_console_line)
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        added = [info["title"] for pid, info in modrinth.installed_projects(target_folder).items() if pid not in before]
        dependencies = added[1:] if len(added) > 1 else []
        message = f"Installed {installed}."
        if dependencies:
            message += f" Also installed required: {', '.join(dependencies)}."
        return {"ok": True, "filename": installed, "dependencies": dependencies, "message": message}
