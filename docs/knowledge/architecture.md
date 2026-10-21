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
- Dashboard: module-level `_render_dashboard_page(section)`; pages (overview, setup, advanced, mods,
  remote, backups, console) are tuples of `_SECTION_*` HTML constants in `_DASHBOARD_PAGES`.
- PlayIt: `_playit_on_exit`, `_playit_state(runtime, cfg)` (running only if the agent process is alive).
- Restart: `_wait_for_exit(process, timeout)`, `RESTART_STOP_TIMEOUT_SEC`.
- Mods folder: `_content_dir_for_config` → `paths.content_folder` (400 for vanilla).

### `webapp/auth.py`
`validate_auth_settings`, `get_expected_credentials`, `verify_credentials` (HTTPBasic),
`verify_basic_auth_header` (manual Base64 for WS; requires `Basic user:pass`).

### `webapp/state.py`
`RuntimeState` dataclass: loader, version, state, operation, install_*, `online_players` (set;
`track_player_event(line)` / `clear_players()`), lan_address,
public_address, playit_*, playit_agent, process, `lock` (RLock);
`snapshot()` returns JSON-safe dict.

### `webapp/websocket_manager.py`
`WebSocketManager`: `history` deque(2000), per-client `asyncio.Queue(250)`, `connect` replays last
500 lines, `publish` thread-safe via `loop.call_soon_threadsafe`, `disconnect` cancels sender.

### `core/` (synchronous, no FastAPI imports; inherited from the former desktop launcher)
- `paths.py` — `DATA_DIR`/`JAVA_DIR`/`SERVERS_DIR`, `ensure_dirs`, `server_dir(loader, version)`
  (creates dir), `content_folder(loader, sdir)` (paper → `plugins/`, fabric/forge/neoforge → `mods/`,
  vanilla → None). Layout: data-model.md.
- `config_store.py` — `load()` → dict (`{}` on missing/invalid JSON); `save(data)` swallows `OSError`.
- `server_process.py` — `accept_eula`, `read_properties`, `update_properties` (read-merge-rewrite via
  `read_properties`), `ServerProcess(args, cwd, on_output, on_exit)` with `start`/`send`/`stop` (sends
  `"stop"`)/`kill` (used by restart on timeout)/`is_running`.
- `java_manager.py` — `required_java(mc_version)`, `ensure_java(feature, log, progress)` → `java.exe` path.
- `downloaders.py` — `list_versions(loader, include_snapshots)`, `prepare(loader, version, sdir, java_exe,
  log, progress)`, `launch_args(loader, java_exe, sdir, ram_mb)`, `download(url, dest, …)` (writes
  `.part` then `os.replace`). `JAR_LOADERS`=vanilla/paper/fabric, `INSTALLER_LOADERS`=forge/neoforge.
- `modrinth.py` — `search(query, loader, game_version, limit)` → `[{title,slug,description,downloads}]`;
  `install(project_ref, loader, game_version, dest_dir, log)` → filename (newest compatible version's
  primary file; `resolve_project_slug` accepts slug or ID).
- `playit.py` — claim/tunnel API client + `PlayitAgent` daemon wrapper (workflows.md §14).
- `netinfo.py` — `lan_ip()` (UDP "connect" to 8.8.8.8, fallback `127.0.0.1`), `public_ip()`
  (`api.ipify.org`, 10 s timeout, returns `"unavailable"` on error; successful lookups cached for
  `PUBLIC_IP_TTL_SEC` = 300 s because the dashboard polls `/api/status`).
- Desktop-launcher leftovers (`crash_reporter.py`, `mailer.py`, `sysinfo.py`,
  `webapp/templates/dashboard.html`) and dead helpers (`netinfo.port_in_use/pids_on_port/free_port`,
  `paths.list_servers`, `playit.is_configured`, `playit.TUNNELS_URL`) were deleted 2026-10-22.
  Everything in `core/` is now used by the web app.
- `requirements.txt`: `jinja2` and `itsdangerous` are not imported anywhere; `python-multipart` is
  needed implicitly by FastAPI `File` uploads.

### Frontend (`webapp/static/js/dashboard.js`, `webapp/templates/login.html`)
- Plain JS, no framework/build. `login.html` checks creds against `GET /api/status`, stores them in
  `sessionStorage`; `dashboard.js::boot` redirects to `/login` if absent, then binds events, loads
  config (+ `/api/versions/{loader}`), status, backups, mods and console. Each loader skips its fetch
  when its elements are not on the current page.
- Polling: metrics 2 s (overview only, probes alias list), console 2 s (`CONSOLE_POLL_MS`, pages with
  `#console-output`), status 10 s (`STATUS_POLL_MS`).
- All calls use `fetchJson` with `Authorization: Basic` from `buildBasicAuth` (upload uses raw `fetch`
  with `FormData`).
- GUI feature status: verification.md.

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
- Endpoint constants: `downloaders.VANILLA_MANIFEST`, `PAPER_API` (v2), `FABRIC_META`, `FORGE_PROMOS`/
  `FORGE_MAVEN`, `NEOFORGE_VERSIONS`/`NEOFORGE_MAVEN`; `java_manager.ADOPTIUM`; `modrinth.API`;
  `playit.API_BASE`, `playit.RELEASES_API` (GitHub latest release); `api.ipify.org`.

## Platform constraints
- Java auto-download is **Windows x64 only**: `java_manager.ADOPTIUM` hard-codes `windows/x64`, and
  `find_java_exe` looks for `java.exe`. PlayIt agent naming supports Linux, but on non-Windows hosts
  a Windows JRE is downloaded and the install/launch subprocess cannot execute it.
- Modern Forge/NeoForge launch relies on `libraries/**/win_args.txt` (`downloaders._find_args_file`).