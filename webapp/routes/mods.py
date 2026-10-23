"""Installed mods/plugins and the Modrinth browser."""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core import mod_updates, modpack, modrinth, paths
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

    # ---------- Client mod pack (.mrpack) for friends (FEAT-04) ----------
    # One build at a time: hashing big jars twice in parallel only wastes the CPU.
    modpack_lock = threading.Lock()

    def ensure_modpack() -> tuple[str, dict[str, Any]]:
        """(pack path, summary), rebuilt only when the mods changed. ValueError when not applicable."""
        config = common._effective_config()
        sdir = common._server_dir_for_config(config)
        with modpack_lock:
            summary = modpack.build(sdir, str(config.get("loader", "vanilla")), str(config.get("version", "")),
                                    str(config.get("motd") or "Minecraft server"),
                                    log=lambda line: append_console_line(f"[modpack] {line}"))
        return modpack.pack_path(sdir), summary

    def modpack_available() -> bool:
        config = common._effective_config()
        loader = str(config.get("loader", "vanilla"))
        folder = paths.content_folder(loader, common._server_dir_for_config(config))
        return modpack.supported(loader) and bool(folder and os.path.isdir(folder)
                                                  and any(n.lower().endswith(".jar") for n in os.listdir(folder)))

    s.ensure_modpack = ensure_modpack
    s.modpack_available = modpack_available

    @app.get("/api/modpack")
    def modpack_info(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = common._effective_config()
        return {"available": modpack_available(), "loader": str(config.get("loader", "vanilla"))}

    @app.post("/api/modpack/build")
    def build_modpack(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            return ensure_modpack()[1]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/modpack/download")
    def download_modpack(_: str = Depends(require_auth)) -> FileResponse:
        try:
            path, summary = ensure_modpack()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(path, media_type="application/x-modrinth-modpack+zip", filename=summary["filename"])

    # The other option: every jar players need as its own download, for launchers without .mrpack import.
    def client_jars() -> dict[str, Any]:
        """{jars: [{name, size}], excluded, warnings}; ValueError when there is no pack to make."""
        config = common._effective_config()
        folder = paths.content_folder(str(config.get("loader", "vanilla")), common._server_dir_for_config(config))
        summary = ensure_modpack()[1]
        jars = [{"name": name, "size": os.path.getsize(os.path.join(folder, name))}
                for name in summary.get("client_jars", []) if os.path.isfile(os.path.join(folder, name))]
        return {"jars": jars, "excluded": summary["excluded"], "warnings": summary["warnings"]}

    def client_jar_response(name: str, **headers: str) -> FileResponse:
        config = common._effective_config()
        try:
            summary = ensure_modpack()[1]
        except ValueError:
            summary = {}
        path = modpack.client_jar_path(common._server_dir_for_config(config), str(config.get("loader", "vanilla")),
                                       summary, name)
        if path is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return FileResponse(path, media_type="application/java-archive", filename=name, headers=headers)

    s.client_jars = client_jars
    s.client_jar_response = client_jar_response

    @app.get("/api/modpack/jars")
    def list_client_jars(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            return client_jars()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/modpack/jars/{name}")
    def download_client_jar(name: str, _: str = Depends(require_auth)) -> FileResponse:
        return client_jar_response(name)

    # ---------- Mod updates (FEAT-07) ----------
    def content_dir() -> tuple[str, str, str]:
        """(loader, game version, mods/plugins folder); 400 for vanilla."""
        config = common._effective_config()
        loader = str(config.get("loader", "vanilla"))
        folder = paths.content_folder(loader, common._server_dir_for_config(config))
        if not folder:
            raise HTTPException(status_code=400, detail="Vanilla servers have no mods to update.")
        return loader, str(config.get("version", "")), folder

    def public_update(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("file", "title", "current", "latest", "changelog", "new_file")}

    def updates_payload(message: str = "", **extra: Any) -> dict[str, Any]:
        try:
            old = mod_updates.old_jars(content_dir()[2])
        except HTTPException:
            old = []
        return {"updates": [public_update(item) for item in runtime.mod_updates], "checked_at": runtime.mod_updates_at,
                "rollback_available": bool(old), "message": message, **extra}

    def check_mod_updates() -> list[dict[str, Any]]:
        loader, version, folder = content_dir()
        runtime.mod_updates = mod_updates.check(folder, loader, version)
        runtime.mod_updates_at = time.time()
        return runtime.mod_updates

    s.check_mod_updates = check_mod_updates

    @app.get("/api/mods/updates")
    def mod_update_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        return updates_payload()

    @app.post("/api/mods/updates/check")
    def check_updates(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            found = check_mod_updates()
        except requests.RequestException as exc:
            raise upstream_error(exc) from exc
        return updates_payload(f"{len(found)} update(s) available." if found else "Everything is up to date.")

    def stopped_and_locked() -> None:
        if s.server_running():
            raise HTTPException(status_code=409, detail="Stop the server first: its mods are in use while it runs.")
        if not runtime.lifecycle.acquire(blocking=False):
            raise s.busy_error()

    @app.post("/api/mods/updates/apply")
    def apply_updates(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        loader, version, folder = content_dir()
        if s.server_running():
            raise HTTPException(status_code=409, detail="Stop the server first: its mods are in use while it runs.")
        wanted = set(payload.get("files") or [item["file"] for item in runtime.mod_updates])
        chosen = [item for item in runtime.mod_updates if item["file"] in wanted]
        if not chosen:
            raise HTTPException(status_code=400, detail="Check for updates first.")
        # Before taking the lifecycle lock: the backup takes it itself while the server is stopped.
        s.safety_backup("mod-update")
        stopped_and_locked()
        updated, failed = [], []
        try:
            for item in chosen:
                try:
                    mod_updates.update(folder, item, loader, version,
                                       log=lambda line: append_console_line(f"[mods] {line}"))
                    updated.append(item["title"])
                    runtime.mod_updates = [u for u in runtime.mod_updates if u["file"] != item["file"]]
                except (OSError, RuntimeError, requests.RequestException) as exc:
                    failed.append(f"{item['title']}: {exc}")
                    append_console_line(f"[mods] update of {item['title']} failed: {exc}")
        finally:
            runtime.lifecycle.release()
        message = f"Updated {len(updated)} mod(s)." if updated else "Nothing was updated."
        if failed:
            message += f" Failed: {'; '.join(failed)}"
        pack_note = bool(updated) and modpack.supported(loader)
        if pack_note:
            message += " Friends using the mod pack should download it again."
        return updates_payload(message, updated=updated, failed=failed, pack_changed=pack_note)

    @app.post("/api/mods/updates/rollback")
    def rollback_updates(_: str = Depends(require_auth)) -> dict[str, Any]:
        _loader, _version, folder = content_dir()
        stopped_and_locked()
        try:
            restored = mod_updates.rollback(folder)
        finally:
            runtime.lifecycle.release()
        append_console_line(f"[mods] rolled back to: {', '.join(restored) or 'nothing'}")
        return updates_payload(f"Restored {len(restored)} previous mod version(s)." if restored else "Nothing to undo.")
