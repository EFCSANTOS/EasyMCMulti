# Architecture

## Stack (verified)
- Python 3.13+, FastAPI, Uvicorn, `fastapi.testclient` for tests (`tests/test_webapp_smoke.py`).
- Optional `psutil` for metrics; Windows `ctypes` fallback in `_system_metrics_snapshot` (`webapp/app.py`).
- No database. Persistence is JSON config + plain-text `server.properties` / `eula.txt`.

## Modules
### `webapp/` (HTTP layer)
- `app.py` — FastAPI factory `create_app()`. Owns `RuntimeState` + `WebSocketManager` on `app.state`.
  Constants: `KNOWN_LOADERS`, `FALLBACK_VERSIONS`. Helpers: `_default_config`, `_effective_config`,
  `_server_properties_from_config`, `_system_metrics_snapshot`, `_server_dir_for_config`,
  `_server_is_installed`, `_playit_on_exit`, `append_console_line`, `update_install_state`,
  `render_dashboard_page`. Auth dependency `require_auth`. *(Route decorators not captured — see verification.md.)*
- `auth.py` — `validate_auth_settings`, `get_expected_credentials`, `verify_credentials` (HTTPBasic),
  `verify_basic_auth_header` (manual Base64 for WebSocket).
- `state.py` — `RuntimeState` dataclass; fields incl. `loader, version, state, operation,
  install_status, install_progress, install_message, players, lan_address, public_address,
  playit_state, playit_address, playit_agent, process, console_history (deque maxlen=2000),
  lock (RLock)`. `snapshot()` returns a JSON-safe dict excluding `process`/`playit_agent`.
- `websocket_manager.py` — `WebSocketManager`: `history` deque(2000), per-client `asyncio.Queue(250)`,
  `connect()` replays last 500 lines, `publish()` is thread-safe via `loop.call_soon_threadsafe`.

### `core/` (runtime)
- `server_process.py` — `accept_eula`, `read_properties`, `update_properties`, `ServerProcess`.
- `config_store.py` — `load()` / (presumed `save()`, used by `/api/config` PUT; not inspected).
- `downloaders.py` — `prepare(loader, version, target_dir, java_exe, log, progress)`, `launch_args(...)`.
- `java_manager.py` — `ensure_java(required)`.
- `modrinth.py` — `install(identifier, loader, version, target_folder, log)`.
- `netinfo.py`, `paths.py` (`server_dir(loader, version)`), `playit.py`, `crash_reporter.py`,
  `mailer.py`, `sysinfo.py` — not inspected.

## Control flow (verified)
1. Uvicorn imports `webapp.app:app`. Module-level `app = create_app()` in tests; prod uses the ASGI app.
2. `create_app()` → `validate_auth_settings()` → build `FastAPI` → attach `runtime` and `console_manager`.
3. Authenticated request → `require_auth` → `verify_credentials` compares against env vars.
4. Server start (per `test_server_start_installs_before_launch`): `_server_is_installed` →
   if missing `downloaders.prepare(...)` → `java_manager.ensure_java` → `downloaders.launch_args` →
   `update_properties(**kwargs)` → `ServerProcess(...).start()`.
5. Console: `ServerProcess.on_output` → `append_console_line` → `WebSocketManager.publish` →
   client queues → `sender()` task → `websocket.send_text`.
6. PlayIt: `playit.load_config` → `ensure_agent` → `ensure_tunnel` → `PlayitAgent.start(secret)`.

## External interfaces
- LAN HTTP on configured port (default 25565 for MC; web default 8000 per README).
- Minecraft server subprocess (java), stdin/stdout piped.
- PlayIt agent subprocess.
- Modrinth HTTP API (via `core/modrinth.py`).
- Mojang/Paper/Fabric/Forge/NeoForge download endpoints (via `core/downloaders.py`).