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
- Auth: `require_auth(request)` → `auth.authenticate` with `app.state.sessions`,
  `app.state.login_limiter`, `app.state.on_lockout`; routes `/api/login`, `/api/logout`, `/api/session`.
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
`validate_auth_settings`, `get_expected_credentials`, `credentials_match` (constant time),
`password_is_weak`, `SessionStore` (in-memory tokens, sliding expiry), `LoginLimiter` (per-IP and
global failure windows), `client_ip`, `check_login` (429 / 401 + count), `authenticate` (cookie or
Basic, works on HTTP and WebSocket; cookie writes pass the same-origin/JSON CSRF check).

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
- `hostenv.py` — `is_windows()`, `is_termux()` (`TERMUX_VERSION` env or `com.termux` in `PREFIX`),
  `termux_prefix()`.
- `java_manager.py` — `required_java(mc_version)`, `ensure_java(feature, log, progress)` → java path.
  Windows: downloads a portable Temurin JRE. Elsewhere: `find_system_java(feature)` scans
  `JAVA_HOME`, `$PREFIX/lib/jvm/*` (Termux), `/usr/lib/jvm/*`, `PATH`; picks the exact major, else
  the oldest newer one; none → on Termux installs `openjdk-17|21|25` automatically
  (`TERMUX_JAVA_VERSIONS`, via `hostenv.termux_install`; Java 8 has no package → error asking for
  MC 1.17+), elsewhere `RuntimeError` with an install hint.
- `hostenv.termux_install(package, log)` — non-interactive `apt-get install -y` (same dpkg options as
  `termux-setup.sh`), refreshes package lists and retries once, one install at a time; failure →
  `RuntimeError` ending in `pkg install <package>`. Used for Java, `cloudflared`, `proot`.
- `downloaders.py` — `list_versions(loader, include_snapshots)`, `prepare(loader, version, sdir, java_exe,
  log, progress)`, `launch_args(loader, java_exe, sdir, ram_mb)`, `download(url, dest, …)` (writes
  `.part` then `os.replace`). `JAR_LOADERS`=vanilla/paper/fabric, `INSTALLER_LOADERS`=forge/neoforge.
- `modrinth.py` — `search(query, loader, game_version, limit)` → `[{title,slug,description,downloads}]`;
  `install(project_ref, loader, game_version, dest_dir, log)` → filename (newest compatible version's
  primary file; `resolve_project_slug` accepts slug or ID).
- `playit.py` — claim/tunnel API client + `PlayitAgent` daemon wrapper (workflows.md §14);
  Minecraft tunnel only.
- `cloudflared.py` — `ensure_binary`, `find_url`, `QuickTunnel` (Cloudflare quick tunnel for the
  web dashboard; workflows.md §16).
- `notifier.py` — alert settings (`load_config`/`save_config`/`public_config`), `send_email`
  (smtplib), `ping_heartbeat` (workflows.md §17).
- `worlds.py` — multiple worlds per server dir: `list_worlds`, `create`, `import_zip`, `rename`,
  `delete`, `export_zip` (workflows.md §19).
- `modrinth.py` — also `categories`, `project` (details), dependency-aware `install`,
  `installed_projects` / `forget_file` (workflows.md §20).
- `updater.py` — `status`, `apply`, `install_requirements`, `restart_process`: self-update from the
  git clone (workflows.md §18). Requires `git` on PATH and a clone (not a zip download).
- `netinfo.py` — `lan_ip()` (UDP "connect" to 8.8.8.8, fallback `127.0.0.1`), `public_ip()`
  (`api.ipify.org`, 10 s timeout, returns `"unavailable"` on error; successful lookups cached for
  `PUBLIC_IP_TTL_SEC` = 300 s because the dashboard polls `/api/status`).
- Desktop-launcher leftovers (`crash_reporter.py`, `mailer.py`, `sysinfo.py`,
  `webapp/templates/dashboard.html`) and dead helpers (`netinfo.port_in_use/pids_on_port/free_port`,
  `paths.list_servers`, `playit.is_configured`, `playit.TUNNELS_URL`) were deleted 2026-10-22.
  Everything in `core/` is now used by the web app.
- `requirements.txt` (runtime): requests, fastapi, plain `uvicorn` + `websockets` (no
  `uvicorn[standard]`: its uvloop/httptools need compiling on Android), python-multipart (FastAPI
  `File` uploads), psutil (skipped on Termux). `requirements-dev.txt` adds pytest + httpx.

### Frontend (`webapp/static/js/dashboard.js`, `webapp/templates/login.html`)
- Plain JS, no framework/build. `login.html` posts to `POST /api/login` (sets the HttpOnly cookie;
  nothing is stored client-side); `dashboard.js::boot` calls `GET /api/session` (401 → `/login`),
  then binds events, loads
  config (+ `/api/versions/{loader}`), status, backups, mods and console. Each loader skips its fetch
  when its elements are not on the current page.
- Polling: metrics 2 s (overview only, `/api/system/metrics`), console 2 s (`CONSOLE_POLL_MS`, pages
  with `#console-output`; re-rendered only when the last line changes), status 5 s (`STATUS_POLL_MS`,
  every page — drives the top-bar state pill and button enablement).
- Layout (2026-10-22 redesign): sidebar nav with inline SVG icons (`_ICON_PATHS` / `_icon`), sticky
  top bar per page (title, subtitle, `#server-state` pill, Start/Restart/Stop), content cards,
  toast `#action-status`. Theme tokens are CSS custom properties at the top of `dashboard.css`
  (dark only; accent `--accent` green; chart series `--series-1` `#3987e5`; status colors reserved
  for state). `login.html` duplicates the tokens inline.
- All calls use `fetchJson` (cookie, `credentials: 'same-origin'`; non-GET always sends JSON, or
  `FormData`); world download/upload use raw `fetch`/XHR with the same cookie. 401 → `/login`.
- GUI feature status: verification.md.

## Control flow (verified)
1. Uvicorn imports `webapp.app:app` → `create_app()` → `validate_auth_settings()` → attach
   `runtime` and `console_manager` to `app.state`.
2. REST request → `require_auth` → session cookie, else Basic credentials (lockout-checked).
3. WS `/ws/console` → `authenticate` (cookie or Basic) → `close(1008)` on failure, else `accept()` →
   `console_manager.connect` → keepalive loop (`wait_for(..., 30s)`).
4. Start: `_effective_config` → `ensure_server_installed` (install if missing) →
   `update_properties` → `ensure_java` → `launch_args` → `ServerProcess.start()`.
5. Console: `ServerProcess._reader` thread → `on_output` → `console_manager.publish` → client queues.
6. PlayIt: `load_config` → `ensure_agent` → `PlayitAgent.start(secret)` → `ensure_tunnel` → `save_config`.

## External interfaces
- LAN HTTP on `WEB_PORT` (`EZ_WEBAPP_PORT`, default 8000; `run.bat`/`run.sh` pass the same value to
  uvicorn); optionally exposed over the internet by a Cloudflare quick tunnel (`core/cloudflared.py`,
  workflows.md §16). MC port from config, optionally exposed by the PlayIt tunnel.
- `cloudflared` subprocess + Cloudflare edge (`*.trycloudflare.com`); binary from
  `github.com/cloudflare/cloudflared` releases (`cloudflared.RELEASE_URL`).
- Java subprocess (stdin/stdout piped, `CREATE_NO_WINDOW` on Windows).
- PlayIt agent subprocess.
- Modrinth HTTP API.
- Mojang/Paper/Fabric/Forge/NeoForge download endpoints.
- Endpoint constants: `downloaders.VANILLA_MANIFEST`, `PAPER_API` (v2), `FABRIC_META`, `FORGE_PROMOS`/
  `FORGE_MAVEN`, `NEOFORGE_VERSIONS`/`NEOFORGE_MAVEN`; `java_manager.ADOPTIUM`; `modrinth.API`;
  `playit.API_BASE`, `playit.RELEASES_API` (GitHub latest release); `api.ipify.org`.

## Platform constraints
Supported hosts: Windows (primary), Linux and Termux on Android (added 2026-10-22, device test pending).
- Java auto-download is Windows x64 only (`java_manager.ADOPTIUM`); Linux/Termux use an installed
  Java. Temurin's Linux builds need glibc and cannot run on Android's bionic libc.
- Termux offers `openjdk-17` and `openjdk-21`: Minecraft 1.17–1.21.x work; ≤1.16 (Java 8) does not;
  26.x needs Java 25 if/when Termux packages it.
- Forge/NeoForge modern launch uses `libraries/**/win_args.txt` on Windows, `unix_args.txt` elsewhere
  (`downloaders._find_args_file`).
- Android blocks `/proc/stat`: CPU shows 0; memory comes from `/proc/meminfo`
  (`app._memory_usage`). Android may also suspend Termux; `run.sh` takes a wake lock.
- PlayIt on Termux uses the Linux binary wrapped in `termux-chroot` (`playit.agent_command`).
- Android 12+ "phantom process killer" can SIGKILL Termux child processes (Java, PlayIt agent);
  the user must disable it (README Android notes). The app cannot work around it.