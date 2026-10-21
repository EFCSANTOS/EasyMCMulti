# Architecture

## Stack (verified)
- Python 3.13+, FastAPI, Uvicorn, `fastapi.testclient` (tests).
- `psutil` optional; Windows `ctypes` fallback (`webapp/app.py::_system_metrics_snapshot`).
- No database. Files: JSON config (`core/config_store`), `server.properties`, `eula.txt`, `world/`,
  `world-backup-*.zip`, `mods/`, `plugins/`.

## Module map
### `webapp/app.py` (HTTP layer, single file)
- Factory `create_app()`; module-level `app = create_app()`.
- Constants: `KNOWN_LOADERS`, `FALLBACK_VERSIONS`, `_APP_START_TIME`.
- Auth: `security = HTTPBasic()`, `require_auth` → `verify_credentials`.
- Config helpers: `_default_config`, `_effective_config`, `_server_properties_from_config`.
- Metrics: `_system_metrics_snapshot`.
- Paths/install: `_server_dir_for_config`, `_server_is_installed`, `ensure_server_installed`.
- Console: `append_console_line`, `WebSocketManager`.
- Dashboard: `render_dashboard_page(section)` (7 sections: overview, setup, advanced, mods, remote, backups, console).
- PlayIt exit hook: `_playit_on_exit`.

### `webapp/auth.py`
`validate_auth_settings`, `get_expected_credentials`, `verify_credentials` (HTTPBasic),
`verify_basic_auth_header` (manual Base64 for WS; requires `Basic user:pass`).

### `webapp/state.py`
`RuntimeState` dataclass: loader, version, state, operation, install_*, players, lan_address,
public_address, playit_*, playit_agent, process, `console_history` (deque 2000), `lock` (RLock);
`snapshot()` returns JSON-safe dict.

### `webapp/websocket_manager.py`
`WebSocketManager`: `history` deque(2000), per-client `asyncio.Queue(250)`, `connect` replays last
500 lines, `publish` thread-safe via `loop.call_soon_threadsafe`, `disconnect` cancels sender.

### `core/`
- `server_process.py` — `accept_eula`, `read_properties`, `update_properties`, `ServerProcess`.
- `config_store.py` — `load()`, `save(config)` (called by `PUT /api/config`, `/api/server/create`, `/api/server/start`).
- `downloaders.py` — `prepare`, `launch_args`, `list_versions`.
- `java_manager.py` — `ensure_java`, `required_java`.
- `modrinth.py` — `search`, `install`.
- `playit.py` — `load_config`, `save_config`, `ensure_agent`, `ensure_tunnel`, `claim_interactive`,
  `PlayitAgent`, `_agent_binary_name`.
- `netinfo.py` — `lan_ip`, `public_ip`.
- `paths.py` — `server_dir(loader, version)`.
- Not inspected: `crash_reporter.py`, `mailer.py`, `sysinfo.py` (no imports from `app.py`).

## Control flow (verified)
1. Uvicorn imports `webapp.app:app` → `create_app()` → `validate_auth_settings()` → attach
   `runtime` and `console_manager` to `app.state`.
2. REST request → `require_auth` → `verify_credentials` (env vars).
3. WS `/ws/console` → `verify_basic_auth_header` → `close(1008)` on failure, else `accept()` →
   `console_manager.connect` → keepalive loop (`wait_for(..., 30s)`).
4. Start: `_effective_config` → `ensure_server_installed` (install if missing) →
   `update_properties` → `ensure_java` → `launch_args` → `ServerProcess.start()`.
5. Console: `ServerProcess._reader` thread → `on_output` → `console_manager.publish` → client queues.
6. PlayIt: `load_config` → `ensure_agent` → `PlayitAgent.start(secret)` → `ensure_tunnel` → `save_config`.

## External interfaces
- LAN HTTP (web port 8000 per README; MC port from config).
- Java subprocess (stdin/stdout piped, `CREATE_NO_WINDOW` on Windows).
- PlayIt agent subprocess.
- Modrinth HTTP API.
- Mojang/Paper/Fabric/Forge/NeoForge download endpoints.