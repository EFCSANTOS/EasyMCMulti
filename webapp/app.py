from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core import config_store, downloaders
from core.server_process import ServerProcess
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

DASHBOARD_HTML = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>MZ Minecraft Server Dashboard</title>
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
      <h1>MZ Minecraft Server</h1>
      <h2>Dashboard</h2>

      <div class=\"card\">
        <form id=\"auth-form\" class=\"row\">
          <div>
            <label for=\"username\">Username</label>
            <input id=\"username\" name=\"username\" value=\"admin\" />
          </div>
          <div>
            <label for=\"password\">Password</label>
            <input id=\"password\" name=\"password\" type=\"password\" value=\"s3cret-pass\" />
          </div>
          <button type=\"submit\">Use credentials</button>
        </form>
      </div>

      <div class=\"card\">
        <div class=\"row\">
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
        username: 'admin',
        password: 's3cret-pass'
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
          'Loader: ' + (status.loader || 'unknown'),
          'Version: ' + (status.version || 'unknown'),
          'Players: ' + (status.players ?? 0),
          'LAN: ' + (status.lan_address || 'unknown'),
          'Public: ' + (status.public_address || 'unknown'),
        ];
        document.getElementById('status-panel').textContent = lines.join('\n');
      }

      function renderConsole(lines) {
        const output = Array.isArray(lines) && lines.length ? lines.join('\n') : 'No console output yet.';
        document.getElementById('console-output').textContent = output;
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


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    return verify_credentials(credentials)


def create_app() -> FastAPI:
    validate_auth_settings()
    app = FastAPI(title="MZ Minecraft Server Launcher", version="0.1.0")
    runtime = RuntimeState()
    app.state.runtime = runtime
    app.state.console_manager = WebSocketManager()

    def append_console_line(line: str) -> None:
        app.state.console_manager.publish(line)

    @app.get("/")
    def root() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/dashboard")
    def dashboard() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "mz-server-webapp"}

    @app.get("/api/status")
    def status(_: str = Depends(require_auth)) -> dict[str, Any]:
        config = config_store.load()
        runtime.loader = config.get("loader", runtime.loader)
        runtime.version = config.get("version", runtime.version)
        return runtime.snapshot()

    @app.get("/api/console")
    def console_history(_: str = Depends(require_auth)) -> dict[str, Any]:
        return {"lines": list(app.state.console_manager.history)}

    @app.get("/api/config")
    def get_config(_: str = Depends(require_auth)) -> dict[str, Any]:
        return config_store.load()

    @app.put("/api/config")
    def save_config(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        config_store.save(payload)
        if "loader" in payload:
            runtime.loader = payload["loader"]
        if "version" in payload:
            runtime.version = payload["version"]
        return payload

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

    @app.post("/api/server/start")
    def start_server(_: str = Depends(require_auth)) -> dict[str, Any]:
        if runtime.process is not None and runtime.process.is_running():
            return {"state": runtime.state, "operation": runtime.operation, "message": "Server already running."}

        runtime.state = "starting"
        runtime.operation = "starting"

        def on_exit(code: int) -> None:
            runtime.state = "stopped"
            runtime.operation = "idle"
            runtime.process = None
            append_console_line(f"[server] exited with code {code}")

        demo_script = (
            "import sys, time; "
            "print('Demo server booting...', flush=True); "
            "for i in range(3): "
            "print(f'demo-line-{i}', flush=True); time.sleep(0.2)"
        )
        runtime.process = ServerProcess(
            [sys.executable, "-c", demo_script],
            cwd=os.getcwd(),
            on_output=append_console_line,
            on_exit=on_exit,
        )
        runtime.process.start()
        runtime.state = "running"
        runtime.operation = "idle"
        append_console_line("[server] started")
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
