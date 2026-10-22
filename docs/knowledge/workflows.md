# Workflows

## 1. Authentication
`create_app()` → `validate_auth_settings()` raises `RuntimeError` if env vars missing.
REST: `require_auth` → `verify_credentials`. WS: `verify_basic_auth_header`; failure → `close(1008)`.
Browser: `/login` form → `GET /api/status` probe → creds in `sessionStorage` → every request sends
Basic header. Sign out clears `sessionStorage` only (no server session exists).

## 2. Dashboard
- `/` → `_render_dashboard_page("overview")`
- `/dashboard` → 302 to `/dashboard/overview`
- `/dashboard/{section}` → page; unknown → 404
- `/dashboard.css`, `/dashboard.js` → `FileResponse` from `webapp/static/...`
- `/login` → `webapp/templates/login.html`
- Every page: top bar with `#server-state` pill and Start/Restart/Stop (enabled by state), toast
  `#action-status`.
- Page contents (`_DASHBOARD_PAGES`): overview = install-progress banner (while installing), tiles
  (server + Backup world, players online, LAN/internet join addresses with copy) + CPU/RAM charts +
  console preview with command input; setup = loader/version/RAM/port form with Save and Create;
  advanced ("Settings") = World fields + Rules switches + sticky Save bar; mods = Modrinth search
  (rows with Install) + installed list with Refresh and Upload .jar (uploads on file select);
  remote = PlayIt card (status badge, address + copy, Configure/Start/Stop) + direct-connection
  addresses; backups = Backup world + rows with Restore (confirm dialog); console = full console with
  auto-scroll toggle + command input, Players (op/deop/kick/ban with confirm) and World shortcuts.
- Loader `<select>` change → `onLoaderChange` → `GET /api/versions/{loader}` → repopulate versions.
- Save (Setup submit or Advanced "Save settings") → `collectConfigFromPage` → `PUT /api/config`
  with only the fields rendered on that page. "Create server" sends the same payload to
  `POST /api/server/create`.

## 3. Install (if missing)
`ensure_server_installed`:
1. `_server_is_installed` → ready.
2. `runtime.state/operation = "installing"`; `update_install_state("installing", 0, ...)`.
3. `java_manager.ensure_java(java_manager.required_java(version))`.
4. `downloaders.prepare(loader, version, target_dir, java_exe, log, progress)`.
5. `accept_eula(target_dir)`.
6. On error → `state="stopped"`, `install_status="failed"`, re-raise.
7. Success → `state="stopped"`, `install_status="ready"`, returns dir.

Java selection (`java_manager.required_java`): `1.21+` and `1.20.5+` → 21; `1.17`–`1.20.4` → 17;
≤`1.16` → 8; non-`1.x` (e.g. `26.2`) or unparsable → 25. `ensure_java` downloads the Temurin JRE zip
from Adoptium, extracts to `java/jre-<N>/`, deletes the zip.

`downloaders.prepare`: jar loaders download `server.jar` (vanilla: Mojang manifest → version meta;
paper: latest build; fabric: latest stable loader+installer server jar). forge/neoforge download
`installer.jar` and run `java -jar installer.jar --installServer` (non-zero exit → `RuntimeError`).
Forge build = `<mc>-recommended` else `<mc>-latest` promo; NeoForge = highest matching build.
Install runs synchronously inside the HTTP request (can take minutes).

## 4. Create server
`POST /api/server/create`: merge payload → save → `ensure_server_installed` →
`update_properties(**_server_properties_from_config)`. Returns state/operation/loader/version/server_dir.

## 5. Start
`POST /api/server/start`:
- If running → `{message:"Server already running."}`.
- Merge payload → save → `state/operation="starting"`.
- Define `on_exit` (console line; state→stopped, process=None **only if `runtime.process` is still
  this process**, so a late exit from a restarted process cannot clobber its replacement).
- `ensure_server_installed` → `update_properties` → `ensure_java` → `launch_args(loader, java, dir, ram)`.
- `ServerProcess(args, cwd, on_output=append_console_line, on_exit=on_exit).start()`.
- `state="running"`, `operation="idle"`, console `"[server] started with {loader} {version}"`.

## 5a. Always-on (config keys `autostart_server`, `autostart_playit`, `auto_restart`)
- `launch_server(payload=None)` holds the start logic; `POST /api/server/start` clears
  `runtime.crash_times` (re-arms crash restarts) and calls it.
- Crash = exit code ≠ 0 while `runtime.stop_requested` is False (set by Stop, Restart, a console
  `stop`/`/stop`, and shutdown; cleared by `launch_server`). With `auto_restart` (default True),
  `handle_crash` schedules `restart_after_crash` after `CRASH_RESTART_DELAY_SEC` (10 s) on a daemon
  `threading.Timer`; more than `CRASH_RESTART_LIMIT` (3) crashes within `CRASH_WINDOW_SEC` (600 s)
  → `state="crashed"` and a console message instead. The timer re-checks `stop_requested` and
  whether a server is already running before launching.
- FastAPI `lifespan` (`_lifespan`): startup calls `app.state.autostart()` — background threads for
  `start_agent_and_tunnels` (if `autostart_playit` and a secret exists) and `launch_server` (if
  `autostart_server`); failures go to the console as `[autostart] …`. `EZ_NO_AUTOSTART=1` skips it.
  Shutdown runs `app.state.shutdown()` in a thread: `stop_requested=True`, graceful stop with
  `SHUTDOWN_STOP_TIMEOUT_SEC` (45 s) then kill, and stops the PlayIt agent, so no orphan Java keeps
  the port or world lock across dashboard restarts. (TestClient without `with` runs neither.)

## 6. Stop / restart / command
- Stop: sets `stop_requested`; if not running → message; else `state/operation="stopping"`,
  `process.stop()` (sends `"stop"`).
- Restart: `stop_server(_)` → `_wait_for_exit(process, RESTART_STOP_TIMEOUT_SEC=60)` → on timeout
  console line + `process.kill()` + wait 10 s → `start_server(None, _)`. Blocks the request while the
  world saves.
- Command: empty → 400; not running → 409; else `process.send(cmd)` + console `"> cmd"`.

## 7. Console
`GET /api/console` → `{lines: list(history)}`. WS replays last 500, then streams via queue.
`publish` trims, appends history, drops oldest when queue full, thread-safe.
Browser: `refreshConsole` polls `GET /api/console` every 2 s (keeps scroll pinned to bottom); the
GUI does not use the WS because browsers cannot send Basic auth on a WebSocket.

## 8. Versions
`GET /api/versions/{loader}`: unknown loader → 400; `downloaders.list_versions(loader, include_snapshots=False)`;
on exception → `FALLBACK_VERSIONS[loader]`; empty → 502; else `{loader, versions}`.
Sources: vanilla releases (manifest order), paper (reversed API list), fabric stable game versions,
forge/neoforge MC versions derived from promos/Maven, sorted by `_version_key` desc.
NeoForge→MC mapping `_neoforge_to_mc`: `21.1.x`→`1.21.1`, `21.0.x`→`1.21`, major ≥25 → `<a>.<b>`.

## 9. Status
`GET /api/status`: refresh loader/version from config; `netinfo.lan_ip()` / `public_ip()` (cached
5 min); `_playit_state` → `"running"` (agent alive) / `"configured"` / `"not-configured"`; address
fallback to public IP; returns `runtime.snapshot()` (PlayIt nested under `playit.{state,address}`).
Browser polls it every 5 s; displayed join addresses append the port from the loaded config.
The Remote page additionally calls `GET /api/playit/status` for the real tunnel address.

## 10. Metrics
Aliases `/api/system/metrics`, `/api/system`, `/api/stats`, `/api/metrics`, `/metrics` →
`{cpu, ram, ramTotal, uptime}` (psutil or Windows ctypes fallback).

## 11. Backups
- Create: zip `world/` → `world-backup-<ts>.zip`; missing world → `{ok:false}`.
- List: `GET /api/server/backups` → `{backups:[...]}`; legacy `GET /api/backups`.
- Restore: 409 if server running; `backup_path`/`name` reduced to basename, must be a
  `world-backup-*.zip` in the current server dir; every zip member must be under `world/` (else 400);
  move current `world/` to `world-backup-pre-restore-<ts>`; extract; legacy `POST /api/backups/restore`.

## 12. Mods
- List: `GET /api/server/mods` (legacy `GET /api/mods`) → `{mods:[{name,path}]}`.
- Target folder (upload + Modrinth install): `_content_dir_for_config` → `paths.content_folder`;
  paper → `plugins/`, fabric/forge/neoforge → `mods/`, vanilla → 400.
- Upload: `POST /api/server/mods/upload` multipart; 400 if no files or no `.jar` among them;
  64 KiB chunks; returns `{ok, saved, message}`.
- Modrinth search: `GET /api/modrinth/search?query=` → `{items}` (limit 10); empty query → 400.
  Facets from `modrinth._LOADER_MAP` (paper → paper/spigot/bukkit/purpur; vanilla → no loader facet)
  plus `versions:<config version>`.
- Modrinth install: `POST /api/modrinth/install` with `slug`/`project_id`/`id`; returns
  `{ok, filename, message}`. The GUI sends the search result's `slug`.

## 13. Admin
`POST /api/admin/{action}`: not running → 409; unknown → 400; player required for op/deop/kick/ban
(checked by action name);
maps to MC commands (`time set day`, `weather clear`, `save-all`, …); sends + logs `"> cmd"`.

## 14. PlayIt
- Status: `configured` = secret present; state from `_playit_state` (`running` only when the agent
  process is alive — a saved address alone no longer reports `running`).
- All PlayIt endpoints return `playit_info()`: `configured`, `state`, `address`, `setup`
  (`idle`/`awaiting-approval`/`connecting`/`error`), `claim_url`, `error`, `dashboard_enabled`,
  `dashboard_address`, `web_port`, `message`.
- Configure (`POST /api/playit/configure`, optional `{reset: true}`): `reset` stops the agent and
  `forget_config()`s the saved account first. If a secret exists → "already configured". Else sets
  `setup="awaiting-approval"`, starts the `claim_and_connect` daemon thread and waits ≤5 s for the
  claim URL so the response carries `claim_url`. The thread: `claim_interactive` (cancellable via
  `runtime.playit_cancel`) → `setup="connecting"` → `start_agent_and_tunnels` → `setup="idle"`;
  any exception → `setup="error"` + `error`. The Remote page polls status every 5 s to show progress.
- `start_agent_and_tunnels(secret)`: start agent if not running → `ensure_tunnel(server_port)` →
  if config `dashboard_tunnel` → `ensure_dashboard_tunnel(WEB_PORT)` → `save_config(secret, address,
  dashboard_address=…)`.
- Start: requires secret; runs `start_agent_and_tunnels` (skipped when already running and no
  dashboard tunnel is wanted).
- Dashboard (`POST /api/playit/dashboard {enabled}`): saves config key `dashboard_tunnel`; enabled +
  agent running → create tunnel now (else on next start); disabled → `delete_dashboard_tunnel` and
  clear `dashboard_address`. Plain HTTP — see verification.md "Must fix".
- Stop: cancels a pending setup, stops agent, state `"stopped"`.
- Config: `GET /api/playit/config` → `{address, secret_present}` (secret never returned).
- Agent exit → `_playit_on_exit` sets state stopped + console line.

Core details (`core/playit.py`):
- `claim_interactive(log, should_cancel, timeout_sec=300, on_url=None)`: random 10-hex claim code →
  `on_url(claim URL)` (no browser is opened; the user opens the link from any device) → polls
  `/claim/setup` every 1.5 s until `UserAccepted` → `/claim/exchange` for `secret_key`.
- `ensure_dashboard_tunnel` / `delete_dashboard_tunnel`: generic TCP tunnel named `EZ Dashboard`
  (`DASHBOARD_TUNNEL_NAME`, `tunnel_type` omitted) to `WEB_PORT`; returns `http://domain:port`.
  Both tunnel kinds share `_ensure_tunnel(..., matches, name, tunnel_type, label)`.
- `agent_command(secret)`: on Termux, prefixes `termux-chroot` (package `proot`) so the agent finds
  `/etc/resolv.conf`; `EZ_PLAYIT_CHROOT=0` disables. Unverified on a device.
- `save_config(secret, address, **extra)` merges extra keys (e.g. `dashboard_address`) into the file.
- `ensure_agent`: reuse existing binary, else download matching asset from GitHub latest release;
  `chmod 755` on non-Windows.
- `ensure_tunnel(secret, local_port, log, timeout_sec=120)`: reuses the account's `minecraft-java`
  tunnel; deletes/recreates it if `local_port` differs; retries create while API says
  `AgentVersionTooOld` (agent must be running first); polls `/tunnels/list` for the allocated address
  (`domain` if SRV, else `domain:port`).
- `_api` reads the JSON body before the HTTP status (API returns 400 with useful `data`); auth header
  `Authorization: Agent-Key <secret>`.
- `PlayitAgent.start(secret)` runs `agent_command(secret)`; `stop()` = `terminate()`.

## 15. Termux (Android) install
`termux-setup.sh` (README: `pkg install -y curl`, download, `bash termux-setup.sh`; piping into bash
also works). All package/git/pip commands run non-interactively with stdin from `/dev/null`
(`DEBIAN_FRONTEND=noninteractive`, dpkg `--force-confdef --force-confold`), because under
`curl | bash` stdin is the script itself and any prompt would consume it. Steps: `pkg update` →
`apt-get full-upgrade` (fresh bootstraps need it) → `apt-get install python git openjdk-21 rust
binutils proot termux-tools` → clone/pull into `~/EasyMCMulti` (`EZ_REPO_URL`, `EZ_BRANCH` default `main`,
`EZ_APP_DIR`, `EZ_JAVA_PACKAGE`) → `.venv` → `pip install` requirements minus `psutil` with
`ANDROID_API_LEVEL` set (pydantic-core compiles via Rust/maturin, 10–20 min) → prompts for login
(12+ char password) into `.env` (mode 600, `%q`-quoted) → installs `$PREFIX/bin/ezmc` (→ `ezmc.sh`)
→ asks "Install 24/7 mode?". Yes → runit service `$PREFIX/var/service/ezmc/run` (exports PREFIX/
HOME/PATH, `exec bash run.sh`; log via `termux-services` svlogger to `$PREFIX/var/log/sv/ezmc/`)
and Termux:Boot script `~/.termux/boot/ezmc-boot` (`termux-wake-lock` + source
`$PREFIX/etc/profile.d/start-services.sh`), then `ezmc start`. No → offers to run in the foreground.
`run.sh` loads `.env`, takes `termux-wake-lock`, prints the LAN URL and **`exec`s** uvicorn on
`EZ_WEBAPP_PORT` (so runit/Ctrl+C signals reach the app's shutdown). `ezmc.sh`: `start` (service
`sv up`, or foreground without a service), `stop` (`sv down` + `termux-wake-unlock`), `restart`,
`status`, `logs` (`tail -F`), `run`; starts runsvdir via start-services.sh if not running.
Re-running the setup updates code/deps and keeps `.env`. Termux:Boot itself must be installed and
opened once by the user (separate APK).