"""Server lifecycle (install/create/start/stop/restart), console commands and admin."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response

from core import activity, config_store, diagnose, downloaders, java_manager, players
from webapp import common
from webapp.services import Services, require_auth
from webapp.validation import require_chat, require_command, require_player, validate_config_update


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line
    notify = s.notify

    def update_install_state(status: str, progress: float, message: str) -> None:
        runtime.install_status = status
        runtime.install_progress = max(0.0, min(float(progress), 100.0))
        runtime.install_message = message

    def ensure_server_installed(config: dict[str, Any]) -> str:
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_dir = common._server_dir_for_config(config)
        if common._server_is_installed(target_dir, loader):
            update_install_state("ready", 100.0, f"{loader} {version} ready.")
            return target_dir

        runtime.state = "installing"
        runtime.operation = "installing"
        update_install_state("installing", 0.0, f"Installing {loader} {version}...")

        def install_log(message: str) -> None:
            if message:
                append_console_line(f"[install] {message}")

        def install_progress(done: int, total: int) -> None:
            if total > 0:
                update_install_state("installing", (done / total) * 100.0, f"Downloading {loader} {version} ({done}/{total} bytes)")
            else:
                update_install_state("installing", 100.0, f"Installing {loader} {version}...")

        try:
            java_exe = java_manager.ensure_java(java_manager.required_java(version), log=install_log)
            downloaders.prepare(loader, version, target_dir, java_exe, log=install_log, progress=install_progress)
            common.accept_eula(target_dir)
        except Exception as exc:
            runtime.state = "stopped"
            runtime.operation = "idle"
            update_install_state("failed", 0.0, str(exc))
            raise

        runtime.state = "stopped"
        runtime.operation = "idle"
        update_install_state("ready", 100.0, f"{loader} {version} installed.")
        return target_dir

    # ---------- Server lifecycle ----------
    # One operation that changes the server process (install, start, stop-and-wait, restart, world
    # switch, restore) runs at a time, under runtime.lifecycle. Routes never wait for it: they get 409
    # "Busy". Slow work runs on a thread so requests return at once (Cloudflare drops requests after
    # ~100 s); the dashboard follows progress through /api/status.
    def busy_error() -> HTTPException:
        label = runtime.operation if runtime.operation != "idle" else "another server operation"
        return HTTPException(status_code=409, detail=f"Busy: {label} in progress. Try again when it finishes.")

    def start_lifecycle_job(operation: str, job: Any, failure: str, state: str | None = None) -> None:
        """Run `job` (returns a completion message) on a thread while holding the lifecycle lock.

        Raises 409 when another lifecycle operation is running. A failure is recorded in
        runtime.operation_error and the console instead of being lost with the request.
        """
        if not runtime.lifecycle.acquire(blocking=False):
            raise busy_error()
        runtime.set_state(state or runtime.state, operation)
        runtime.operation_error = ""
        runtime.operation_message = ""

        def worker() -> None:
            try:
                runtime.operation_message = job()
            except Exception as exc:
                runtime.set_state("running" if server_running() else "stopped", "idle")
                runtime.operation_error = f"{failure}: {exc}"
                append_console_line(f"[server] {runtime.operation_error}")
            finally:
                runtime.lifecycle.release()

        threading.Thread(target=worker, name=f"lifecycle-{operation}", daemon=True).start()

    def accepted(response: Response, message: str, **extra: Any) -> dict[str, Any]:
        response.status_code = 202
        return {"state": runtime.state, "operation": runtime.operation, "message": message, **extra}

    def save_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
        config = common._effective_config()
        if payload:
            config.update(validate_config_update(payload, common._host_ram_mb()))
            config_store.save(config)
        return config

    def install_server(config: dict[str, Any]) -> str:
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        runtime.loader = loader
        runtime.version = version
        runtime.set_state("ready", "creating")
        target_dir = ensure_server_installed(config)
        common.update_properties(target_dir, **common._server_properties_from_config(config))
        runtime.set_state("stopped", "idle")
        return f"Server {loader} {version} is ready."

    @app.post("/api/server/create")
    def create_server(response: Response, payload: dict[str, Any] | None = None,
                      _: str = Depends(require_auth)) -> dict[str, Any]:
        if server_running():
            raise HTTPException(status_code=409, detail="Stop the server before creating it again.")
        config = save_payload(payload)
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        start_lifecycle_job("creating", lambda: install_server(config), "Server creation failed")
        return accepted(response, f"Creating {loader} {version}; downloads can take a few minutes.",
                        loader=loader, version=version)

    def handle_crash(code: int, cause: str = "") -> None:
        """Schedule an automatic restart, unless the server keeps crashing. `cause`: diagnosis line."""
        now = time.monotonic()
        runtime.crash_times = [t for t in runtime.crash_times if now - t < common.CRASH_WINDOW_SEC]
        runtime.crash_times.append(now)
        attempt = len(runtime.crash_times)
        if attempt > common.CRASH_RESTART_LIMIT:
            runtime.state = "crashed"
            message = (
                f"The server crashed {attempt} times in {common.CRASH_WINDOW_SEC // 60} minutes; automatic restart paused. "
                "Check the console log, fix the problem and press Start."
            )
            append_console_line(f"[server] {message}")
            notify("server", "Minecraft server is DOWN (auto-restart paused)", f"{message}\n\n{cause}".strip())
            return
        message = (
            f"The server crashed (exit code {code}); restarting in {common.CRASH_RESTART_DELAY_SEC} s "
            f"(attempt {attempt} of {common.CRASH_RESTART_LIMIT})."
        )
        append_console_line(f"[server] {message}")
        notify("server", "Minecraft server crashed", f"{message}\n\n{cause}".strip())
        timer = threading.Timer(common.CRASH_RESTART_DELAY_SEC, restart_after_crash)
        timer.daemon = True
        timer.start()

    def launch_when_free(reason: str) -> bool:
        """For background callers (crash timer, autostart): wait for the lifecycle lock, then start
        the server unless a stop was requested or something else already started it."""
        if not runtime.lifecycle.acquire(timeout=common.LIFECYCLE_WAIT_SEC):
            raise RuntimeError(f"another server operation is still running; {reason} skipped")
        try:
            if runtime.stop_requested and reason == "crash restart":
                return False  # the user stopped the server in the meantime
            if reason in ("autostart", "crash restart") and common._in_quiet_hours(common._effective_config()):
                append_console_line(f"[schedule] quiet hours: {reason} skipped")
                return False
            if server_running():
                return False
            launch_server()
            return True
        finally:
            runtime.lifecycle.release()

    def restart_after_crash() -> None:
        try:
            restarted = launch_when_free("crash restart")
        except Exception as exc:
            runtime.set_state("stopped", "idle")
            append_console_line(f"[server] automatic restart failed: {exc}")
            notify("server", "Minecraft server is DOWN (restart failed)", f"Automatic restart failed: {exc}")
            return
        if restarted:
            notify("server", "Minecraft server is back online", "The server was restarted automatically after a crash.")

    def launch_server(payload: dict[str, Any] | None = None) -> str:
        """Install if needed and start the Minecraft server. Caller holds runtime.lifecycle."""
        config = save_payload(payload)
        loader = str(config.get("loader", runtime.loader))
        version = str(config.get("version", runtime.version))
        runtime.loader = loader
        runtime.version = version
        runtime.set_state("starting", "starting")
        runtime.stop_requested = False

        process: Any = None

        def on_exit(code: int) -> None:
            append_console_line(f"[server] exited with code {code}")
            if runtime.process is not process:
                return  # a newer process already replaced this one (e.g. after a restart)
            activity.record("server_stop", server_dir=target_dir)  # closes everyone's open session
            runtime.set_state("stopped", "idle")
            runtime.process = None
            runtime.clear_players()
            if code == 0 or runtime.stop_requested:
                if runtime.stopped_reason != "idle":  # the idle stop already told them why
                    s.notify_friends("status", "Server is offline.")
                return
            cause = diagnose.first_line(s.run_diagnosis())
            if common._effective_config().get("auto_restart"):
                s.notify_friends("status", "Server crashed; it restarts by itself in a moment.")
                handle_crash(code, cause)
            else:
                s.notify_friends("status", "Server stopped unexpectedly and is offline.")
                notify("server", "Minecraft server stopped unexpectedly",
                       f"The server exited with code {code}. Automatic restart is off.\n\n{cause}".strip())

        def on_output(line: str) -> None:
            event = runtime.track_player_event(line)
            if event:
                s.player_event(*event)
                activity.record("join" if event[1] == "joined" else "leave", event[0], target_dir)
            refused = players.rejected_name(line)
            if refused:
                runtime.note_player(refused, rejected=True)
            append_console_line(line)

        target_dir = ensure_server_installed(config)
        runtime.set_state("starting", "starting")
        common.update_properties(target_dir, **common._server_properties_from_config(config))

        java_exe = java_manager.ensure_java(java_manager.required_java(version),
                                            log=lambda message: append_console_line(f"[java] {message}"))
        args = downloaders.launch_args(loader, java_exe, target_dir, int(config.get("ram_mb", 2048)))
        process = common.ServerProcess(
            args,
            cwd=target_dir,
            on_output=on_output,
            on_exit=on_exit,
        )
        runtime.clear_players()
        runtime.process = process
        runtime.last_start_at = time.time()
        s.clear_diagnosis()
        process.start()
        runtime.set_state("running", "idle")
        runtime.empty_since = time.time()  # the idle timer runs until someone joins
        runtime.stopped_reason = ""
        append_console_line(f"[server] started with {loader} {version}")
        address = s.join_address(config)
        s.notify_friends("status", f"Server is online: {common.LOADER_LABELS.get(loader, loader)} {version}"
                         + (f" at {address}" if address else "") + ".")
        return "Server started."

    def request_stop() -> str:
        runtime.stop_requested = True  # also cancels a pending crash restart
        if runtime.process is None or not runtime.process.is_running():
            runtime.set_state("stopped", "idle")
            return "Server is already stopped."
        runtime.set_state("stopping", "stopping")
        runtime.process.stop()
        return "Stop requested."

    def stop_and_wait() -> None:
        """Stop the server and wait for the process to exit (kill it if it hangs). Caller holds the lock."""
        process = runtime.process
        request_stop()
        if process is not None and not common._wait_for_exit(process, common.RESTART_STOP_TIMEOUT_SEC):
            append_console_line("[server] did not stop in time; killing the process")
            process.kill()
            common._wait_for_exit(process, 10)

    @app.post("/api/server/start")
    def start_server(response: Response, payload: dict[str, Any] | None = None,
                     _: str = Depends(require_auth)) -> dict[str, Any]:
        if server_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}
        save_payload(payload)
        runtime.crash_times = []  # a manual start re-arms automatic crash restarts
        start_lifecycle_job("starting", launch_server, "Server start failed", state="starting")
        return accepted(response, "Starting the server…")

    @app.post("/api/server/stop")
    def stop_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if not runtime.lifecycle.acquire(blocking=False):
            raise busy_error()
        try:
            message = request_stop()
        finally:
            runtime.lifecycle.release()
        return {"state": runtime.state, "operation": runtime.operation, "message": message}

    def restart_job() -> str:
        stop_and_wait()
        return launch_server()

    @app.post("/api/server/restart")
    def restart_server(response: Response, _: str = Depends(require_auth)) -> dict[str, Any]:
        start_lifecycle_job("restarting", restart_job, "Server restart failed")
        return accepted(response, "Restarting the server…")

    @app.post("/api/server/chat")
    def send_chat(payload: dict[str, Any], username: str = Depends(require_auth)) -> dict[str, Any]:
        """Say something in game as "[Dashboard] <user>: <message>" (FEAT-11)."""
        message = require_chat(payload.get("message"))
        if runtime.process is None or not runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Server is not running.")
        # Built with json.dumps, never by concatenating the message into the command.
        components = ["", {"text": "[Dashboard] ", "color": "gray"}, {"text": f"{username}: ", "color": "aqua"},
                      {"text": message}]
        runtime.process.send("tellraw @a " + json.dumps(components, ensure_ascii=False))
        append_console_line(f"[chat] {username}: {message}")
        return {"ok": True, "message": "Sent."}

    @app.post("/api/server/command")
    def send_command(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        command = require_command(payload.get("command"))
        if runtime.process is None or not runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Server is not running.")
        if command.lstrip("/").lower() == "stop":
            runtime.stop_requested = True  # a console "stop" is deliberate, not a crash
        runtime.process.send(command)
        append_console_line(f"> {command}")
        return {"ok": True, "command": command}

    def server_running() -> bool:
        return bool(runtime.process is not None and runtime.process.is_running())

    @app.post("/api/admin/{action}")
    def admin_action(action: str, payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is None or not runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Server is not running.")

        action = action.lower()
        player = (payload or {}).get("player")
        if action in {"op", "deop", "kick", "ban"}:
            player = require_player(player)
        command_map = {
            "op": f"op {player}" if player else "op",
            "deop": f"deop {player}" if player else "deop",
            "kick": f"kick {player}" if player else "kick",
            "ban": f"ban {player}" if player else "ban",
            "day": "time set day",
            "night": "time set night",
            "clear": "weather clear",
            "rain": "weather rain",
            "save": "save-all",
        }
        if action not in command_map:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")

        command = command_map[action]
        runtime.process.send(command)
        append_console_line(f"> {command}")
        return {"ok": True, "action": action, "command": command, "message": f"{action} command sent."}


    s.ensure_server_installed = ensure_server_installed
    s.busy_error = busy_error
    s.start_lifecycle_job = start_lifecycle_job
    s.launch_server = launch_server
    s.launch_when_free = launch_when_free
    s.restart_after_crash = restart_after_crash
    s.request_stop = request_stop
    s.stop_and_wait = stop_and_wait
    s.server_running = server_running
    s.stop_server = stop_server
    s.restart_job = restart_job
