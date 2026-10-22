"""Always-on behaviour: autostart on boot and clean shutdown."""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from fastapi import FastAPI

from core import playit
from webapp import common
from webapp.services import Services
from webapp.auth import password_is_weak


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line
    notify = s.notify

    def run_in_background(name: str, action: Any) -> None:
        def worker() -> None:
            try:
                action()
            except Exception as exc:
                append_console_line(f"[autostart] {name} failed: {exc}")
                category = "server" if name == "Minecraft server" else "tunnels"
                notify(category, f"{name} failed to start", str(exc))

        threading.Thread(target=worker, name=f"autostart-{name}", daemon=True).start()

    def autostart() -> None:
        """Bring up what the Always-on settings ask for. EZ_NO_AUTOSTART=1 skips it (debugging)."""
        if common._effective_config().get("remote_dashboard") and password_is_weak():
            warning = ("[auth] WARNING: the remote dashboard link is on and EZ_WEBAPP_PASSWORD is shorter than "
                       "12 characters. Anyone with the link can try to guess it; set a longer password.")
            logging.getLogger("uvicorn.error").warning(warning)
            append_console_line(warning)
        if os.getenv("EZ_NO_AUTOSTART") == "1":
            return
        threading.Thread(target=s.heartbeat_loop, name="heartbeat", daemon=True).start()
        threading.Thread(target=s.scheduler_loop, name="scheduler", daemon=True).start()
        threading.Thread(target=s.update_check_loop, name="update-check", daemon=True).start()
        config = common._effective_config()
        resume = s.take_resume_marker()  # what was running before an in-app update restarted us
        secret = playit.load_config().get("secret")
        if (config.get("autostart_playit") or resume.get("playit")) and secret:
            append_console_line("[autostart] starting the PlayIt tunnel")
            run_in_background("PlayIt tunnel", lambda: s.start_agent_and_tunnels(secret))
        if config.get("autostart_server") or resume.get("server"):
            append_console_line("[autostart] starting the Minecraft server")
            run_in_background("Minecraft server", lambda: s.launch_when_free("autostart"))
        if config.get("remote_dashboard"):
            append_console_line("[autostart] starting the remote dashboard link")
            run_in_background("remote dashboard", s.start_web_tunnel_safely)

    def shutdown() -> None:
        """Stop Minecraft and the PlayIt agent with the dashboard, so no orphan keeps the port or world lock."""
        runtime.shutting_down = True
        runtime.stop_requested = True
        process = runtime.process
        if process is not None and process.is_running():
            append_console_line("[server] dashboard shutting down; stopping the server")
            process.stop()
            if not common._wait_for_exit(process, common.SHUTDOWN_STOP_TIMEOUT_SEC):
                process.kill()
        s.stop_playit_agent()
        s.stop_web_tunnel()


    s.autostart = autostart
    s.shutdown = shutdown
    s.run_in_background = run_in_background
