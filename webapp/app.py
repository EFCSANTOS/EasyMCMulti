from __future__ import annotations

import asyncio
import glob
import os
import sys
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import config_store, downloaders, java_manager, netinfo, paths, playit
from core.server_process import ServerProcess, accept_eula
from webapp.auth import validate_auth_settings, verify_basic_auth_header, verify_credentials
from webapp.state import RuntimeState
from webapp.websocket_manager import WebSocketManager

KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
FALLBACK_VERSIONS = {
    "vanilla": ["1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.20.4"],
    "paper": ["1.21.7", "1.21.6", "1.21.4", "1.21.1", "1.20.4"],
    "fabric": ["1.21.7", "1.21.6", "1.21.4", "1.20.4"],
    "forge": ["1.20.1", "1.19.2", "1.16.5"],
    "neoforge": ["1.21.7", "1.21.6", "1.21.1", "1.20.1"],
}

security = HTTPBasic()

DASHBOARD_HTML_TEMPLATE = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>EZ Minecraft Server Dashboard</title>
    <style>
      body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem; }
      .wrap { max-width: 980px; margin: 0 auto; }
      h1 { margin-bottom: 0.25rem; }
      .card { background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
      .row { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; }
      .row input, .row button, .row textarea { font: inherit; }
      .row input, .row textarea { background: #0b1120; color: #e2e8f0; border: 1px solid #475569; border-radius: 8px; padding: 0.65rem 0.75rem; }
      button { background: #2563eb; color: white; border: none; border-radius: 8px; padding: 0.7rem 1rem; cursor: pointer; }
      button.secondary { background: #374151; }
      button.danger { background: #dc2626; }
      pre { white-space: pre-wrap; word-break: break-word; background: #020817; border-radius: 8px; padding: 1rem; min-height: 120px; }
      label { display: block; margin-bottom: 0.45rem; color: #cbd5e1; }
      .status { font-weight: 600; }
    </style>
  </head>
  <body>
    <div class=\"wrap\">
      <h1>EZ Minecraft Server</h1>
      <h2>Dashboard</h2>

      <div class=\"card\">
        <form id=\"auth-form\" class=\"row\">
          <div>
            <label for=\"username\">Username</label>
            <input id=\"username\" name=\"username\" value=\"__DEFAULT_USERNAME__\" />
          </div>
          <div>
            <label for=\"password\">Password</label>
            <input id=\"password\" name=\"password\" type=\"password\" value=\"__DEFAULT_PASSWORD__\" />
          </div>
          <button type=\"submit\">Use credentials</button>
        </form>
      </div>

      <div class=\"card\">        <form id="setup-form" class="row">
          <div>
            <label for="loader-select">Loader</label>
            <select id="loader-select" name="loader">
              <option value="vanilla">vanilla</option>
              <option value="paper">paper</option>
              <option value="fabric">fabric</option>
              <option value="forge">forge</option>
              <option value="neoforge">neoforge</option>
            </select>
          </div>
          <div>
            <label for="version-select">Version</label>
            <select id="version-select" name="version"></select>
          </div>
          <div>
            <label for="ram-input">RAM (MB)</label>
            <input id="ram-input" name="ram_mb" type="number" min="512" step="256" value="2048" />
          </div>
          <button type="submit">Save config</button>
          <button id="create-button" type="button">Create server</button>
        </form>
      </div>

      <div class="card">        <div class=\"row\">
          <button id=\"start-button\">Start server</button>
          <button id=\"stop-button\" class=\"danger\">Stop server</button>
        </div>
      </div>

      <div class=\"card\">
        <div class=\"status\" id=\"status-panel\">Loading status...</div>
      </div>

      <div class=\"card\">
        <form id=\"command-form\" class=\"row\">
          <div style=\"flex:1\">
            <label for=\"command-input\">Console command</label>
            <input id=\"command-input\" name=\"command\" placeholder=\"say hello\" />
          </div>
          <button type=\"submit\">Send</button>
        </form>
      </div>

      <div class=\"card\">
        <h3>Console</h3>
        <pre id=\"console-output\">Waiting for logs...</pre>
      </div>
    </div>

    <script>
      const state = {
        username: '__DEFAULT_USERNAME__',
        password: '__DEFAULT_PASSWORD__'
      };

      function getAuthHeader() {
        const user = state.username || '';
        const pass = state.password || '';
        return 'Basic ' + btoa(user + ':' + pass);
      }

      async function apiRequest(path, options = {}) {
        const headers = Object.assign({}, options.headers || {});
        headers.Authorization = getAuthHeader();
        const response = await fetch(path, Object.assign({ headers }, options));
        const text = await response.text();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (error) { data = text; }
        if (!response.ok) {
          throw new Error(data && data.detail ? data.detail : 'Request failed');
        }
        return data;
      }

      function renderStatus(payload) {
        const status = payload || {};
        const lines = [
          'State: ' + (status.state || 'unknown'),
          'Operation: ' + (status.operation || 'unknown'),
          'Install: ' + (status.install_status || 'ready') + ' (' + ((status.install_progress ?? 0) | 0) + '%)',
          'Loader: ' + (status.loader || 'unknown'),
          'Version: ' + (status.version || 'unknown'),
          'Players: ' + (status.players ?? 0),
          'LAN: ' + (status.lan_address || 'unknown'),
          'Public: ' + (status.public_address || 'unknown'),
        ];
        if (status.install_message) {
          lines.push('Message: ' + status.install_message);
        }
        document.getElementById('status-panel').textContent = lines.join('\n');
      }

      function renderConsole(lines) {
        const output = Array.isArray(lines) && lines.length ? lines.join('\n') : 'No console output yet.';
        document.getElementById('console-output').textContent = output;
      }

      async function loadVersions(loader) {
        try {
          const payload = await apiRequest('/api/versions/' + loader);
          const versionSelect = document.getElementById('version-select');
          versionSelect.innerHTML = '';
          for (const version of payload.versions || []) {
            const option = document.createElement('option');
            option.value = version;
            option.textContent = version;
            versionSelect.appendChild(option);
          }
        } catch (error) {
          const versionSelect = document.getElementById('version-select');
          versionSelect.innerHTML = '<option value="">Unavailable</option>';
        }
      }

      async function refreshConfig() {
        try {
          const payload = await apiRequest('/api/config');
          const loader = document.getElementById('loader-select');
          const version = document.getElementById('version-select');
          const ram = document.getElementById('ram-input');
          loader.value = payload.loader || 'vanilla';
          ram.value = payload.ram_mb || 2048;
          await loadVersions(loader.value);
          if (payload.version) {
            version.value = payload.version;
          }
        } catch (error) {
          // keep defaults
        }
      }

      async function refreshStatus() {
        try {
          const payload = await apiRequest('/api/status');
          renderStatus(payload);
        } catch (error) {
          document.getElementById('status-panel').textContent = 'Status unavailable: ' + error.message;
        }
      }

      async function refreshConsole() {
        try {
          const payload = await apiRequest('/api/console');
          renderConsole(payload && Array.isArray(payload.lines) ? payload.lines : []);
        } catch (error) {
          document.getElementById('console-output').textContent = 'Console unavailable: ' + error.message;
        }
      }

      document.getElementById('auth-form').addEventListener('submit', (event) => {
        event.preventDefault();
        state.username = document.getElementById('username').value;
        state.password = document.getElementById('password').value;
        refreshStatus();
        refreshConsole();
      });

      document.getElementById('loader-select').addEventListener('change', async (event) => {
        await loadVersions(event.target.value);
      });

      document.getElementById('setup-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const payload = {
          loader: document.getElementById('loader-select').value,
          version: document.getElementById('version-select').value,
          ram_mb: Number(document.getElementById('ram-input').value || 2048),
        };
        try {
          await apiRequest('/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          await refreshStatus();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('create-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/create', { method: 'POST' });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('start-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/start', { method: 'POST' });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('stop-button').addEventListener('click', async () => {
        try {
          await apiRequest('/api/server/stop', { method: 'POST' });
          await refreshStatus();
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      document.getElementById('command-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = document.getElementById('command-input');
        const command = input.value.trim();
        if (!command) return;
        try {
          await apiRequest('/api/server/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
          });
          input.value = '';
          await refreshConsole();
        } catch (error) {
          alert(error.message);
        }
      });

      refreshConfig();
      refreshStatus();
      refreshConsole();
      setInterval(() => {
        refreshStatus();
        refreshConsole();
      }, 2000);
    </script>
  </body>
</html>
"""


def _dashboard_html() -> str:
    username = os.getenv("EZ_WEBAPP_USERNAME", "admin")
    password = os.getenv("EZ_WEBAPP_PASSWORD", "admin")
    return DASHBOARD_HTML_TEMPLATE.replace("__DEFAULT_USERNAME__", username).replace("__DEFAULT_PASSWORD__", password)


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    return verify_credentials(credentials)


def _default_config() -> dict[str, Any]:
    return {
        "loader": "vanilla",
        "version": "1.21.7",
        "ram_mb": 2048,
        "server_port": 25565,
    }


def _effective_config() -> dict[str, Any]:
    config = config_store.load()
    merged = _default_config()
    if isinstance(config, dict):
        merged.update(config)
    return merged


def _server_dir_for_config(config: dict[str, Any]) -> str:
    loader = str(config.get("loader", "vanilla"))
    version = str(config.get("version", "1.21.7"))
    return paths.server_dir(loader, version)


def _server_is_installed(target_dir: str, loader: str) -> bool:
    if loader in {"vanilla", "paper", "fabric"}:
        return os.path.exists(os.path.join(target_dir, "server.jar"))

    if loader in {"forge", "neoforge"}:
        return bool(
            glob.glob(os.path.join(target_dir, f"{loader}-*.jar"))
            or glob.glob(os.path.join(target_dir, f"{loader}*.jar"))
            or glob.glob(os.path.join(target_dir, "libraries", "**", "win_args.txt"), recursive=True)
        )
    return False


def create_app() -> FastAPI:
    validate_auth_settings()
    app = FastAPI(title="EZ Minecraft Server Launcher", version="0.1.0")
    runtime = RuntimeState()
    app.state.runtime = runtime
    app.state.console_manager = WebSocketManager()

    def append_console_line(line: str) -> None:
        app.state.console_manager.publish(line)

    def update_install_state(status: str, progress: float, message: str) -> None:
        runtime.install_status = status
        runtime.install_progress = max(0.0, min(float(progress), 100.0))
        runtime.install_message = message

    def ensure_server_installed(config: dict[str, Any]) -> str:
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        target_dir = _server_dir_for_config(config)
        if _server_is_installed(target_dir, loader):
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
            java_exe = java_manager.ensure_java(java_manager.required_java(version))
            downloaders.prepare(loader, version, target_dir, java_exe, log=install_log, progress=install_progress)
            accept_eula(target_dir)
        except Exception as exc:
            runtime.state = "stopped"
            runtime.operation = "idle"
            update_install_state("failed", 0.0, str(exc))
            raise

        runtime.state = "stopped"
        runtime.operation = "idle"
        update_install_state("ready", 100.0, f"{loader} {version} installed.")
        return target_dir

    @app.get("/")
    def root() -> HTMLResponse:
        return HTMLResponse(_dashboard_html())

    @app.get("/dashboard")
    def dashboard() -> HTMLResponse:
        return HTMLResponse(_dashboard_html())

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "ez-server-webapp"}

    @app.get("/api/status")
    def status(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        runtime.loader = str(config.get("loader", runtime.loader))
        runtime.version = str(config.get("version", runtime.version))
        runtime.lan_address = netinfo.lan_ip()
        runtime.public_address = netinfo.public_ip()
        playit_cfg = playit.load_config()
        runtime.playit_state = "configured" if playit_cfg.get("secret") else "not-configured"
        runtime.playit_address = playit_cfg.get("address") or runtime.public_address
        return runtime.snapshot()

    @app.get("/api/console")
    def console_history(_: str = Depends(require_auth)) -> dict[str, Any]:
        return {"lines": list(app.state.console_manager.history)}

    @app.get("/api/config")
    def get_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return _effective_config()

    @app.put("/api/config")
    def save_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config = dict(payload)
        config_store.save(config)
        if "loader" in config:
            runtime.loader = str(config["loader"])
        if "version" in config:
            runtime.version = str(config["version"])
        return config

    @app.get("/api/versions/{loader}")
    def versions(loader: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        if loader not in KNOWN_LOADERS:
            raise HTTPException(status_code=400, detail=f"Unsupported loader: {loader}")

        try:
            versions = downloaders.list_versions(loader, include_snapshots=False)
        except Exception:
            versions = FALLBACK_VERSIONS.get(loader, [])

        if not versions:
            raise HTTPException(status_code=502, detail=f"No versions available for loader: {loader}")

        return {"loader": loader, "versions": versions}

    @app.post("/api/server/create")
    def create_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        loader = str(config.get("loader", "vanilla"))
        version = str(config.get("version", "1.21.7"))
        runtime.loader = loader
        runtime.version = version
        runtime.operation = "creating"
        runtime.state = "ready"
        try:
            target_dir = ensure_server_installed(config)
        except Exception as exc:
            runtime.operation = "idle"
            runtime.state = "stopped"
            raise HTTPException(status_code=500, detail=f"Server creation failed: {exc}") from exc

        runtime.operation = "idle"
        runtime.state = "stopped"
        return {"state": runtime.state, "operation": runtime.operation, "loader": loader, "version": version, "server_dir": target_dir}

    @app.post("/api/server/start")
    def start_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}

        config = _effective_config()
        loader = str(config.get("loader", runtime.loader))
        version = str(config.get("version", runtime.version))
        runtime.loader = loader
        runtime.version = version
        runtime.state = "starting"
        runtime.operation = "starting"

        def on_exit(code: int) -> None:
            runtime.state = "stopped"
            runtime.operation = "idle"
            runtime.process = None
            append_console_line(f"[server] exited with code {code}")

        target_dir = ensure_server_installed(config)

        java_exe = java_manager.ensure_java(java_manager.required_java(version))
        args = downloaders.launch_args(loader, java_exe, target_dir, int(config.get("ram_mb", 2048)))
        runtime.process = ServerProcess(
            args,
            cwd=target_dir,
            on_output=append_console_line,
            on_exit=on_exit,
        )
        runtime.process.start()
        runtime.state = "running"
        runtime.operation = "idle"
        append_console_line(f"[server] started with {loader} {version}")
        return {"state": runtime.state, "operation": runtime.operation, "message": "Server started."}

    @app.post("/api/server/stop")
    def stop_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is None or not runtime.process.is_running():
            runtime.state = "stopped"
            runtime.operation = "idle"
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server is already stopped."}

        runtime.state = "stopping"
        runtime.operation = "stopping"
        runtime.process.stop()
        return {"state": runtime.state, "operation": runtime.operation, "message": "Stop requested."}

    @app.post("/api/server/restart")
    def restart_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        stop_server(_)
        return start_server(_)

    @app.post("/api/server/command")
    def send_command(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        command = str(payload.get("command", "")).strip()
        if not command:
            raise HTTPException(status_code=400, detail="A command is required.")
        if runtime.process is None or not runtime.process.is_running():
            raise HTTPException(status_code=409, detail="Server is not running.")
        runtime.process.send(command)
        append_console_line(f"> {command}")
        return {"ok": True, "command": command}

    @app.post("/api/server/backup")
    def backup_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        world_dir = os.path.join(target_dir, "world")
        if not os.path.isdir(world_dir):
            return {"ok": False, "message": "No world directory found for backup."}
        archive = os.path.join(target_dir, f"world-backup-{__import__('time').strftime('%Y%m%d-%H%M%S')}.zip")
        import zipfile
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(world_dir):
                for name in files:
                    abs_path = os.path.join(root, name)
                    rel_path = os.path.relpath(abs_path, target_dir)
                    zf.write(abs_path, rel_path)
        return {"ok": True, "archive": archive}

    @app.get("/api/server/backups")
    def list_backups(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = _effective_config()
        target_dir = _server_dir_for_config(config)
        backups = []
        for entry in os.listdir(target_dir):
            if entry.startswith("world-backup-") and entry.endswith(".zip"):
                backups.append(os.path.join(target_dir, entry))
        return {"backups": backups}

    @app.get("/api/playit/status")
    def playit_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        return {
            "configured": bool(cfg.get("secret")),
            "state": "configured" if cfg.get("secret") else "not-configured",
            "address": cfg.get("address") or "",
            "secret_present": bool(cfg.get("secret")),
        }

    @app.post("/api/playit/start")
    def start_playit(_: str = Depends(require_auth)) -> dict[str, Any]:
        cfg = playit.load_config()
        if not cfg.get("secret"):
            return {"configured": False, "state": "not-configured", "address": "", "message": "PlayIt is not configured yet."}
        return {
            "configured": True,
            "state": "configured",
            "address": cfg.get("address") or "",
            "message": "PlayIt agent status is available.",
        }

    @app.get("/api/playit/config")
    def playit_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return playit.load_config()

    @app.websocket("/ws/console")
    async def console_websocket(websocket: WebSocket) -> None:
        auth_header = websocket.headers.get("authorization")
        try:
            verify_basic_auth_header(auth_header)
        except HTTPException:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        await app.state.console_manager.connect(websocket)
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    break
        finally:
            await app.state.console_manager.disconnect(websocket)

    return app


app = create_app()
