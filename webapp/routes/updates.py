"""Self-update (git) and alert settings."""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import notifier, paths, updater
from webapp import common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line
    notify = s.notify

    # ---------- Self-update ----------
    def update_log(line: str) -> None:
        append_console_line(f"[update] {line}")

    def resume_marker_path() -> str:
        return os.path.join(paths.DATA_DIR, "resume_after_update.json")

    def take_resume_marker() -> dict[str, Any]:
        """Read and delete the marker written before an update restart."""
        path = resume_marker_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        try:
            os.remove(path)
        except OSError:
            pass
        return data if isinstance(data, dict) else {}

    def update_payload(message: str = "") -> dict[str, Any]:
        config = common._effective_config()
        return {
            **(runtime.update_info or {}),
            "state": runtime.update_state,
            "error": runtime.update_error,
            "message": message or runtime.update_message,
            "auto_check": bool(config.get("auto_update_check")),
            "auto_install": bool(config.get("auto_update_install")),
        }

    def check_for_updates(fetch: bool = True) -> dict[str, Any]:
        runtime.update_info = updater.status(fetch=fetch)
        return runtime.update_info

    def run_update() -> None:
        """Pull, then restart onto the new code (resuming Minecraft/PlayIt if they were running)."""
        runtime.update_state = "updating"
        runtime.update_error = ""
        runtime.update_message = "Downloading the update..."
        try:
            result = updater.apply(update_log)
        except Exception as exc:
            runtime.update_state = "error"
            runtime.update_error = str(exc)
            update_log(f"Update failed: {exc}")
            notify("updates", "Update failed", str(exc))
            return
        if not result.get("updated"):
            runtime.update_state = "idle"
            runtime.update_message = "Already up to date."
            return
        runtime.update_message = "Backing up the world before restarting..."
        s.safety_backup("update")  # best effort: a failure is alerted, the update goes on
        resume = {
            "server": bool(runtime.process is not None and runtime.process.is_running()),
            "playit": bool(runtime.playit_agent is not None and runtime.playit_agent.is_running()),
        }
        paths.ensure_dirs()
        with open(resume_marker_path(), "w", encoding="utf-8") as handle:
            json.dump(resume, handle)
        runtime.update_state = "restarting"
        runtime.update_message = f"Updated {result['from']} -> {result['to']}; restarting the dashboard..."
        update_log(runtime.update_message)
        time.sleep(1)  # let the browser receive the "restarting" state
        s.shutdown()
        updater.restart_process()

    def update_check_loop() -> None:
        time.sleep(common.UPDATE_FIRST_CHECK_SEC)
        while not runtime.shutting_down:
            config = common._effective_config()
            if config.get("auto_update_check") and runtime.update_state in {"idle", "error"}:
                try:
                    info = check_for_updates()
                except Exception as exc:
                    update_log(f"Update check failed: {exc}")
                    info = {}
                if info.get("behind") and info.get("latest_hash") != runtime.update_notified_hash:
                    runtime.update_notified_hash = info["latest_hash"]
                    changes = "\n".join(f"- {c['subject']} ({c['when']})" for c in info.get("commits", []))
                    notify("updates", "Update available", f"{info['behind']} new change(s) on {info['upstream']}:\n\n"
                           f"{changes}\n\nOpen Server setup > Updates to install.", throttle=False)
                busy = runtime.state in {"starting", "stopping", "installing"} or bool(runtime.online_players)
                if info.get("behind") and config.get("auto_update_install") and not busy and not info.get("local_changes"):
                    update_log("Installing the update automatically (no one is online).")
                    run_update()
            time.sleep(common.UPDATE_CHECK_INTERVAL_SEC)

    @app.get("/api/update")
    def update_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.update_info is None:
            try:
                check_for_updates(fetch=False)
            except Exception as exc:
                runtime.update_info = {"available": False, "reason": str(exc)}
        return update_payload()

    @app.post("/api/update/check")
    def update_check(_: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            info = check_for_updates(fetch=True)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Update check failed: {exc}") from exc
        if not info.get("available"):
            return update_payload(info.get("reason", ""))
        if info["behind"]:
            return update_payload(f"{info['behind']} update(s) available.")
        return update_payload("You are on the latest version.")

    @app.post("/api/update/apply")
    def update_apply(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.update_state in {"updating", "restarting"}:
            return update_payload("An update is already running.")
        runtime.update_state = "updating"
        threading.Thread(target=run_update, name="update", daemon=True).start()
        return update_payload("Updating; the dashboard will restart by itself.")

    @app.get("/api/notifications")
    def get_notifications(_: str = Depends(require_auth)) -> dict[str, Any]:
        return notifier.public_config()

    @app.put("/api/notifications")
    def save_notifications(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        payload.pop("last_link", None)
        try:
            notifier.save_config(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return notifier.public_config()

    @app.post("/api/notifications/test")
    def test_notifications(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = notifier.load_config()
        results = []
        if config.get("email_to") and config.get("smtp_user") and config.get("smtp_password"):
            try:
                notifier.send_email(config, "[EZ Minecraft] Test alert",
                                    "Alerts are working. You will get an email like this when the server "
                                    "crashes, the dashboard link changes, or a tunnel has a problem.")
                results.append("Test email sent.")
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Email failed: {exc}") from exc
        for key, label, text in (
            ("discord_owner_url", "owner", "Alerts are working: crashes, outages and new dashboard links will be posted here."),
            ("discord_friends_url", "friends", "Hello from the Minecraft server! You'll see when it goes online or "
                                               "offline and who joins."),
        ):
            if config.get(key):
                try:
                    notifier.send_discord(config[key], text)
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f"Discord ({label} channel) failed: {exc}") from exc
                results.append(f"Discord {label} channel OK.")
        if config.get("heartbeat_url"):
            if not notifier.ping_heartbeat(config["heartbeat_url"]):
                raise HTTPException(status_code=502, detail="Heartbeat URL did not answer.")
            results.append("Heartbeat ping OK.")
        if not results:
            raise HTTPException(status_code=400, detail="Fill in the email settings, a Discord webhook or a heartbeat URL first.")
        return {"ok": True, "message": " ".join(results)}


    s.take_resume_marker = take_resume_marker
    s.update_check_loop = update_check_loop
