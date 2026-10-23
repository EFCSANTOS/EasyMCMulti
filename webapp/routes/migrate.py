"""Move the server to another Minecraft version or loader, bringing worlds and settings (FEAT-09)."""
from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import Depends, FastAPI, HTTPException, Response

from core import backups, config_store, migrate
from webapp import common
from webapp.services import Services, require_auth
from webapp.validation import validate_config_update


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line

    def target(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """(current config, config for the target loader/version), validated."""
        config = common._effective_config()
        wanted = validate_config_update({"loader": payload.get("loader"), "version": payload.get("version")},
                                        common._host_ram_mb())
        if set(wanted) != {"loader", "version"}:
            raise HTTPException(status_code=400, detail="Choose a loader and a version.")
        if wanted["loader"] == config.get("loader") and wanted["version"] == config.get("version"):
            raise HTTPException(status_code=400, detail="That is already the current version.")
        return config, {**config, **wanted}

    def make_plan(config: dict[str, Any], new_config: dict[str, Any]) -> dict[str, Any]:
        old_sdir = common._server_dir_for_config(config)
        new_sdir = common._server_dir_for_config(new_config)
        try:
            return migrate.plan(old_sdir, str(config.get("loader")), str(config.get("version")),
                                str(new_config["loader"]), str(new_config["version"]), new_sdir)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Modrinth is not reachable to check the mods: {exc}") from exc

    @app.post("/api/server/migrate/plan")
    def migration_plan(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config, new_config = target(payload)
        return make_plan(config, new_config)

    @app.post("/api/server/migrate")
    def migrate_server(payload: dict[str, Any], response: Response, _: str = Depends(require_auth)) -> dict[str, Any]:
        config, new_config = target(payload)
        if s.server_running():
            raise HTTPException(status_code=409, detail="Stop the server before moving it to another version.")
        the_plan = make_plan(config, new_config)
        if the_plan["downgrade"] and not payload.get("allow_downgrade"):
            raise HTTPException(status_code=409, detail="That is an older version: worlds opened in a newer version "
                                                        "usually can't be opened by an older one. Confirm to copy anyway.")
        if not the_plan["enough_space"]:
            raise HTTPException(status_code=409, detail="Not enough free space to copy the worlds. Free some space on "
                                                        "the Backups page (Storage) first.")
        if the_plan["mods"]["missing"] and not payload.get("skip_missing"):
            names = ", ".join(m["title"] for m in the_plan["mods"]["missing"])
            raise HTTPException(status_code=409, detail=f"No {new_config['version']} version yet for: {names}.")
        old_sdir = common._server_dir_for_config(config)
        new_sdir = common._server_dir_for_config(new_config)
        label = f"{common.LOADER_LABELS.get(new_config['loader'], new_config['loader'])} {new_config['version']}"

        def job() -> str:
            # Holding the lifecycle lock: back up with core.backups directly (the FEAT-01 helper takes the lock).
            active = common._active_world(config)
            if os.path.isdir(os.path.join(old_sdir, active)):
                append_console_line("[migrate] backing up the current world first")
                backups.create(old_sdir, active, "auto-pre-migrate")
            s.ensure_server_installed(new_config)
            installed = migrate.apply(the_plan, old_sdir, new_sdir, log=lambda line: append_console_line(f"[migrate] {line}"))
            config_store.save(new_config)
            runtime.loader, runtime.version = str(new_config["loader"]), str(new_config["version"])
            append_console_line(f"[migrate] moved to {label}: {len(the_plan['worlds'])} world folder(s), "
                                f"{len(installed)} mod(s)")
            if the_plan["downgrade"]:
                return (f"Copied to {label}. Worlds from a newer version may not load here; the newer version is "
                        f"untouched if you switch back.")
            return (f"Moved to {label}. The first start upgrades the world, so it can take longer than usual. "
                    f"The old version is untouched if you switch back.")

        s.start_lifecycle_job("migrating", job, "Moving to the new version failed", state="installing")
        response.status_code = 202
        return {"state": runtime.state, "operation": runtime.operation, "message": f"Moving to {label}…"}
