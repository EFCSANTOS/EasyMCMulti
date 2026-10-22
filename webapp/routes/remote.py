"""Remote access: PlayIt tunnel and the Cloudflare dashboard link."""
from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import cloudflared, config_store, notifier, playit
from webapp import common
from webapp.auth import password_is_weak
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    runtime = s.runtime
    append_console_line = s.append_console_line
    notify = s.notify

    def playit_log(line: str) -> None:
        append_console_line(f"[playit] {line}")

    def playit_info(message: str = "") -> dict[str, Any]:
        cfg = playit.load_config()
        return {
            "configured": bool(cfg.get("secret")),
            "state": common._playit_state(runtime, cfg),
            "address": cfg.get("address") or "",
            "secret_present": bool(cfg.get("secret")),
            "setup": runtime.playit_setup,
            "claim_url": runtime.playit_claim_url or "",
            "error": runtime.playit_error,
            "message": message,
        }

    def start_agent_and_tunnels(secret: str) -> None:
        """Start the agent if needed, ensure the Minecraft tunnel and save its address."""
        cancel_playit_restart()  # this start supersedes any pending automatic restart
        key_rejected = threading.Event()
        if not (runtime.playit_agent and runtime.playit_agent.is_running()):
            playit.ensure_agent(log=playit_log)
            playit.kill_stale_agents(log=playit_log)  # only one agent may run per device
            agent: Any = None
            last_error = [""]

            def on_agent_output(line: str) -> None:
                append_console_line(line)
                if "error" in line.lower():
                    last_error[0] = line.strip()
                if key_rejected.is_set() or not playit.is_key_rejected(line):
                    return
                # A rejected key never recovers by retrying: stop the agent (deliberately, so it is not
                # restarted as a crash) and ask the owner to link the launcher again.
                key_rejected.set()
                runtime.playit_setup = "error"
                runtime.playit_error = common.PLAYIT_KEY_REJECTED
                playit_log(common.PLAYIT_KEY_REJECTED)
                notify("tunnels", "PlayIt key rejected", common.PLAYIT_KEY_REJECTED)
                if runtime.playit_agent is agent:
                    stop_playit_agent()
                    runtime.playit_state = "stopped"

            def on_agent_exit(code: int) -> None:
                # Deliberate stops clear runtime.playit_agent first, so only a crash still finds itself here.
                if runtime.playit_agent is not agent:
                    append_console_line(f"[playit] exited with code {code}")
                    return
                common._playit_on_exit(app.state.console_manager, runtime, code)
                if runtime.shutting_down:
                    return
                now = time.monotonic()
                runtime.playit_crash_times = [t for t in runtime.playit_crash_times if now - t < common.PLAYIT_CRASH_WINDOW_SEC]
                runtime.playit_crash_times.append(now)
                if len(runtime.playit_crash_times) > common.PLAYIT_CRASH_LIMIT:
                    message = (f"The PlayIt agent stopped {len(runtime.playit_crash_times)} times in "
                               f"{common.PLAYIT_CRASH_WINDOW_SEC // 60} minutes, so automatic restarts are paused. "
                               f"Last error: {last_error[0] or f'exit code {code}'}. Click Start tunnel to try again.")
                    runtime.playit_setup = "error"
                    runtime.playit_error = message
                    playit_log(message)
                    notify("tunnels", "PlayIt tunnel DOWN (auto-restart paused)", message)
                    return
                message = f"The PlayIt agent exited (code {code}); restarting it in {common.PLAYIT_RETRY_SEC} s."
                playit_log(message)
                notify("tunnels", "PlayIt tunnel stopped unexpectedly", message)
                schedule_playit_restart(secret)

            agent = playit.PlayitAgent(on_output=on_agent_output, on_exit=on_agent_exit)
            runtime.playit_agent = agent
            agent.start(secret)

        def should_abort() -> str | None:
            if key_rejected.is_set():
                return common.PLAYIT_KEY_REJECTED
            if not (runtime.playit_agent and runtime.playit_agent.is_running()):
                return "The PlayIt agent stopped before the tunnel was ready."
            return None

        config = common._effective_config()
        address = playit.ensure_tunnel(secret, int(config.get("server_port", 25565)), playit_log,
                                       should_abort=should_abort)
        playit.save_config(secret, address)
        runtime.playit_state = "running"
        runtime.playit_address = address

    def schedule_playit_restart(secret: str) -> None:
        """Restart the agent after common.PLAYIT_RETRY_SEC; replaces any restart already pending."""
        cancel_playit_restart()
        timer = threading.Timer(common.PLAYIT_RETRY_SEC, lambda: s.run_in_background("PlayIt tunnel", lambda: start_agent_and_tunnels(secret)))
        timer.daemon = True
        runtime.playit_restart_timer = timer
        timer.start()

    def cancel_playit_restart() -> None:
        timer = runtime.playit_restart_timer
        runtime.playit_restart_timer = None
        if timer is not None:
            timer.cancel()

    def stop_playit_agent() -> None:
        """Deliberate stop: forget the agent first so its exit is not treated as a crash."""
        cancel_playit_restart()
        agent = runtime.playit_agent
        runtime.playit_agent = None
        if agent is not None and agent.is_running():
            agent.stop()

    def claim_and_connect() -> None:
        """Background PlayIt setup: wait for the user's approval, then bring the tunnels up."""
        try:
            secret = playit.claim_interactive(
                playit_log, lambda: runtime.playit_cancel, on_url=lambda url: setattr(runtime, "playit_claim_url", url)
            )
            runtime.playit_setup = "connecting"
            runtime.playit_claim_url = None
            start_agent_and_tunnels(secret)
            runtime.playit_setup = "idle"
            playit_log("Setup complete; tunnel running.")
        except Exception as exc:
            runtime.playit_claim_url = None
            if runtime.playit_cancel:
                runtime.playit_setup = "idle"
                playit_log("Setup cancelled.")
                return
            runtime.playit_setup = "error"
            runtime.playit_error = str(exc)
            playit_log(f"Setup failed: {exc}")

    @app.get("/api/playit/status")
    def playit_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        return playit_info()

    @app.post("/api/playit/configure")
    def configure_playit(payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.playit_setup in {"awaiting-approval", "connecting"}:
            return playit_info("PlayIt setup already in progress.")
        if (payload or {}).get("reset"):
            # Link a different account: stop the agent and forget the saved secret first.
            stop_playit_agent()
            playit.forget_config()
            playit_log("Saved PlayIt account removed.")
        cfg = playit.load_config()
        if cfg.get("secret"):
            runtime.playit_address = cfg.get("address") or runtime.playit_address
            return playit_info("PlayIt already configured.")

        runtime.playit_setup = "awaiting-approval"
        runtime.playit_claim_url = None
        runtime.playit_error = ""
        runtime.playit_cancel = False
        threading.Thread(target=claim_and_connect, daemon=True).start()
        deadline = time.monotonic() + 5
        while runtime.playit_claim_url is None and runtime.playit_setup == "awaiting-approval" and time.monotonic() < deadline:
            time.sleep(0.05)
        return playit_info("Open the approval link and click Allow.")

    @app.post("/api/playit/start")
    def start_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        if not cfg.get("secret"):
            return playit_info("PlayIt is not configured yet. Click Configure first.")
        if runtime.playit_agent and runtime.playit_agent.is_running():
            return playit_info("PlayIt tunnel is already running.")
        if runtime.playit_setup == "error":  # a new attempt clears the previous failure
            runtime.playit_setup = "idle"
            runtime.playit_error = ""
        runtime.playit_crash_times.clear()  # a manual start re-arms automatic restarts
        try:
            start_agent_and_tunnels(cfg["secret"])
        except Exception as exc:
            playit_log(f"Start failed: {exc}")
            raise HTTPException(status_code=502, detail=f"PlayIt start failed: {exc}") from exc
        return playit_info("PlayIt tunnel started.")

    @app.post("/api/playit/stop")
    def stop_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.playit_setup in {"awaiting-approval", "connecting"}:
            runtime.playit_cancel = True
        stop_playit_agent()
        cfg = playit.load_config()
        runtime.playit_state = "stopped"
        runtime.playit_address = cfg.get("address") or runtime.playit_address
        return playit_info("PlayIt tunnel stopped.")

    @app.get("/api/playit/config")
    def playit_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        # Never expose the secret key over HTTP; it stays in playit_config.json on the host.
        cfg = playit.load_config()
        return {"address": cfg.get("address") or "", "secret_present": bool(cfg.get("secret"))}

    @app.put("/api/playit/config")
    def save_playit_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        """Edit playit_config.json by hand: paste a secret key from playit.gg or fix the address.

        A blank or missing secret keeps the saved one (it is never sent to the browser).
        Cancels a pending approval; a changed secret stops the running agent.
        """
        unknown = set(payload) - {"secret", "address"}
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown keys: {', '.join(sorted(unknown))}. Allowed: secret, address.")
        secret = payload.get("secret") or ""
        address = payload.get("address") or ""
        if not isinstance(secret, str) or not isinstance(address, str):
            raise HTTPException(status_code=400, detail="secret and address must be text.")
        if runtime.playit_setup == "connecting":
            raise HTTPException(status_code=409, detail="PlayIt is connecting; wait for it to finish, then save.")
        if runtime.playit_setup == "awaiting-approval":
            runtime.playit_cancel = True
            runtime.playit_setup = "idle"
            runtime.playit_claim_url = None
        runtime.playit_error = ""

        saved = playit.load_config().get("secret") or ""
        secret = secret.strip() or saved
        if not secret:
            raise HTTPException(status_code=400, detail="No saved secret key: paste one into \"secret\".")
        if secret != saved and not playit.is_valid_secret(secret):
            raise HTTPException(status_code=400, detail="That is not a playit secret key: it should be the long "
                                                        "hexadecimal key (0-9, a-f) from playit.gg.")
        if secret != saved:
            stop_playit_agent()
            runtime.playit_state = "stopped"
        playit.save_config(secret, address.strip())
        runtime.playit_address = address.strip() or runtime.playit_address
        playit_log("PlayIt config saved from the dashboard.")
        return playit_info("PlayIt config saved. Click Start tunnel to use it.")

    # ---------- Remote dashboard (Cloudflare quick tunnel) ----------
    def web_log(line: str) -> None:
        append_console_line(f"[cloudflare] {line}")

    def remote_info(message: str = "") -> dict[str, Any]:
        return {
            "enabled": bool(common._effective_config().get("remote_dashboard")),
            "state": runtime.web_tunnel_state,
            "url": runtime.web_tunnel_url or "",
            "error": runtime.web_tunnel_error,
            "weak_password": password_is_weak(),
            "message": message,
        }

    def start_web_tunnel() -> None:
        if runtime.web_tunnel is not None and runtime.web_tunnel.is_running():
            return
        runtime.web_tunnel_wanted = True
        runtime.web_tunnel_state = "starting"
        runtime.web_tunnel_error = ""
        runtime.web_tunnel_url = None
        binary = cloudflared.ensure_binary(log=web_log)
        tunnel: Any = None

        def on_url(url: str) -> None:
            runtime.web_tunnel_url = url
            runtime.web_tunnel_state = "running"
            web_log(f"Dashboard reachable at {url}")
            if url != notifier.load_config().get("last_link"):
                notifier.save_config({"last_link": url})
                body = f"Your dashboard is now at:\n\n{url}\n\n"
                config = common._effective_config()
                if config.get("status_page_enabled") and config.get("status_page_token"):
                    status_url = f"{url}/s/{config['status_page_token']}"
                    body += f"Status page for friends:\n\n{status_url}\n\n"
                    s.notify_friends("link", f"Server status page (new link): {status_url}")
                notify("link", "New dashboard link",
                       body + "The old link no longer works. New links can take up to a minute to open.", throttle=False)

        def on_output(line: str) -> None:
            if " ERR " in line or " WRN " in line:  # cloudflared is chatty; keep problems only
                web_log(line)

        def on_exit(code: int) -> None:
            if runtime.web_tunnel is not tunnel:
                return
            runtime.web_tunnel = None
            runtime.web_tunnel_url = None
            if not runtime.web_tunnel_wanted:
                runtime.web_tunnel_state = "off"
                return
            runtime.web_tunnel_state = "error"
            runtime.web_tunnel_error = f"cloudflared exited (code {code}); retrying in {common.WEB_TUNNEL_RETRY_SEC} s"
            web_log(runtime.web_tunnel_error)
            notify("tunnels", "Dashboard link went down",
                   f"{runtime.web_tunnel_error}. You will get the new link by email when it is back.")
            timer = threading.Timer(common.WEB_TUNNEL_RETRY_SEC, retry_web_tunnel)
            timer.daemon = True
            timer.start()

        tunnel = cloudflared.QuickTunnel(on_url=on_url, on_output=on_output, on_exit=on_exit)
        runtime.web_tunnel = tunnel
        tunnel.start(binary, common.WEB_PORT)

    def start_web_tunnel_safely() -> None:
        try:
            start_web_tunnel()
        except Exception as exc:
            runtime.web_tunnel_state = "error"
            runtime.web_tunnel_error = str(exc)
            web_log(f"Start failed: {exc}")
            notify("tunnels", "Dashboard link could not start", str(exc))

    def retry_web_tunnel() -> None:
        if runtime.web_tunnel_wanted and common._effective_config().get("remote_dashboard"):
            start_web_tunnel_safely()

    def stop_web_tunnel() -> None:
        runtime.web_tunnel_wanted = False
        tunnel = runtime.web_tunnel
        runtime.web_tunnel = None
        runtime.web_tunnel_url = None
        runtime.web_tunnel_state = "off"
        runtime.web_tunnel_error = ""
        if tunnel is not None and tunnel.is_running():
            tunnel.stop()

    @app.get("/api/remote-dashboard")
    def remote_dashboard_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        return remote_info()

    @app.post("/api/remote-dashboard")
    def remote_dashboard_toggle(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        enabled = bool(payload.get("enabled"))
        config = common._effective_config()
        config["remote_dashboard"] = enabled
        config_store.save(config)
        if not enabled:
            stop_web_tunnel()
            return remote_info("The dashboard is no longer reachable from the internet.")
        runtime.web_tunnel_state = "starting"
        runtime.web_tunnel_error = ""
        threading.Thread(target=start_web_tunnel_safely, name="cloudflared", daemon=True).start()
        return remote_info("Starting the Cloudflare tunnel; the link appears in a few seconds.")


    s.start_agent_and_tunnels = start_agent_and_tunnels
    s.stop_playit_agent = stop_playit_agent
    s.start_web_tunnel_safely = start_web_tunnel_safely
    s.stop_web_tunnel = stop_web_tunnel
