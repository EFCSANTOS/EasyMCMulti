# Workflows

## 1. Authentication
`create_app()` → `validate_auth_settings()` raises `RuntimeError` if env vars missing.
REST and WS: `auth.authenticate` accepts the `ezmc_session` cookie or HTTP Basic; WS failure →
`close(1008)`.
Browser: `/login` form → `POST /api/login {username, password}` → `Set-Cookie: ezmc_session`
(HttpOnly, SameSite=Strict, Secure when `X-Forwarded-Proto: https` / `CF-Connecting-IP`, 7 days,
sliding) → the browser sends it on every request. Sign out → `POST /api/logout` deletes the session.
Lockout: a wrong password (login or Basic) counts per client IP (`CF-Connecting-IP` only when the
peer is loopback, i.e. cloudflared); 5 in 15 min → 429 + `Retry-After` for 15 min; 30 across all IPs
→ everyone locked for 15 min. First lockout → `[auth]` console line + `security` alert. A live
session keeps working during a lockout. Startup logs a warning when the link is on and the password
is under 12 characters; `GET /api/remote-dashboard` returns `weak_password` for the Remote page banner.

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
Install runs on a background lifecycle job (§5b), never inside the HTTP request (can take minutes;
the Cloudflare link drops requests after ~100 s).

## 4. Create server
`POST /api/server/create`: 409 if the server runs; merge payload → save → **202**
`{state, operation:"creating", loader, version, message}`; the job (`install_server`) runs
`ensure_server_installed` → `update_properties(**_server_properties_from_config)` and ends with
`operation_message="Server <loader> <version> is ready."`.

## 5. Start
`POST /api/server/start`:
- If running → 200 `{message:"Server already running."}`.
- Merge payload → save → clear `crash_times` → **202** `state/operation="starting"`; the job runs
  `launch_server()` (below) and ends with `operation_message="Server started."`.
- Define `on_exit` (console line; state→stopped, process=None **only if `runtime.process` is still
  this process**, so a late exit from a restarted process cannot clobber its replacement).
- `ensure_server_installed` → `update_properties` → `ensure_java` → `launch_args(loader, java, dir, ram)`.
- `ServerProcess(args, cwd, on_output=append_console_line, on_exit=on_exit).start()`.
- `state="running"`, `operation="idle"`, console `"[server] started with {loader} {version}"`.

## 5a. Always-on (config keys `autostart_server`, `autostart_playit`, `auto_restart`)
- `launch_server(payload=None)` holds the start logic (caller holds the lifecycle lock);
  `POST /api/server/start` clears `runtime.crash_times` (re-arms crash restarts) and runs it as a job.
- Crash = exit code ≠ 0 while `runtime.stop_requested` is False (set by Stop, Restart, a console
  `stop`/`/stop`, and shutdown; cleared by `launch_server`). With `auto_restart` (default True),
  `handle_crash` schedules `restart_after_crash` after `CRASH_RESTART_DELAY_SEC` (10 s) on a daemon
  `threading.Timer`; more than `CRASH_RESTART_LIMIT` (3) crashes within `CRASH_WINDOW_SEC` (600 s)
  → `state="crashed"` and a console message instead. The timer calls `launch_when_free`, which
  waits up to `LIFECYCLE_WAIT_SEC` (600 s) for the lifecycle lock, then re-checks `stop_requested`
  and whether a server is already running before launching. Autostart/resume use the same helper.
- FastAPI `lifespan` (`_lifespan`): startup calls `app.state.autostart()` — background threads for
  `start_agent_and_tunnels` (if `autostart_playit` and a secret exists) and `launch_server` (if
  `autostart_server`); failures go to the console as `[autostart] …`. `EZ_NO_AUTOSTART=1` skips it.
  Shutdown runs `app.state.shutdown()` in a thread: `stop_requested=True`, graceful stop with
  `SHUTDOWN_STOP_TIMEOUT_SEC` (45 s) then kill, and stops the PlayIt agent, so no orphan Java keeps
  the port or world lock across dashboard restarts. (TestClient without `with` runs neither.)

## 5b. Lifecycle jobs and the lifecycle lock (FIX-02 / FIX-04)
- `runtime.lifecycle` (non-reentrant `threading.Lock`) is held for the whole of any operation that
  changes the server process: create/install, start, stop, restart, world switch, backup restore.
- Routes never wait: `lifecycle.acquire(blocking=False)` or **409** `"Busy: <operation> in progress…"`.
  Background callers (crash timer, autostart, resume after update) use `launch_when_free`.
- `start_lifecycle_job(operation, job, failure, state=None)`: takes the lock, sets
  state/operation, clears `operation_error`/`operation_message`, runs `job` on a daemon thread and
  releases the lock when it ends. Success → `operation_message` = the job's return value. Failure →
  state `running`/`stopped` by whether a process is alive, `operation="idle"`,
  `operation_error="<failure>: <exc>"` and a `[server]` console line (an install failure also sets
  `install_status="failed"`).
- State changes go through `RuntimeState.set_state(state, operation)` (under `runtime.lock`).
- Browser: lifecycle buttons get 202 + a message toast; `trackOperation` marks the operation as
  running; the status poll runs every 1 s (`STATUS_BUSY_POLL_MS`) while `operation != "idle"` or an
  install runs, else every 5 s, and on busy → idle toasts `operation_error` (error) or
  `operation_message`. Start/Stop/Restart stay disabled while an operation runs. The install banner
  (`_INSTALL_BANNER`) is on every page.

## 6. Stop / restart / command
- Stop: 409 if a lifecycle job runs; sets `stop_requested`; if not running → message; else
  `state/operation="stopping"`, `process.stop()` (sends `"stop"`); returns at once.
- Restart: **202**, job `restart_job` = `stop_and_wait()` (`request_stop` →
  `_wait_for_exit(process, RESTART_STOP_TIMEOUT_SEC=60)` → on timeout console line +
  `process.kill()` + wait 10 s) → `launch_server()`.
- Command: empty → 400; not running → 409; else `process.send(cmd)` + console `"> cmd"`.

## 7. Console
`GET /api/console` → `{lines: list(history)}`. WS replays last 500, then streams via queue.
`publish` trims, appends history, drops oldest when queue full, thread-safe.
Browser: `refreshConsole` polls `GET /api/console` every 2 s (keeps scroll pinned to bottom); the
GUI does not use the WS yet (it accepts the session cookie since FIX-01; FEAT-11 switches over).

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
- Create: zip the **active** world (+ dimension folders) → `world-backup-<folder>-<ts>.zip`
  (see §19); not generated yet → `{ok:false}`.
- List: `GET /api/server/backups` → `{backups:[...]}`; legacy `GET /api/backups`.
- Restore: 409 if a lifecycle job runs (Busy) or the server runs; holds the lifecycle lock while
  extracting; `backup_path`/`name` reduced to basename, must be a
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
  (`idle`/`awaiting-approval`/`connecting`/`error`), `claim_url`, `error`, `message`.
- Configure (`POST /api/playit/configure`, optional `{reset: true}`): `reset` stops the agent and
  `forget_config()`s the saved account first. If a secret exists → "already configured". Else sets
  `setup="awaiting-approval"`, starts the `claim_and_connect` daemon thread and waits ≤5 s for the
  claim URL so the response carries `claim_url`. The thread: `claim_interactive` (cancellable via
  `runtime.playit_cancel`) → `setup="connecting"` → `start_agent_and_tunnels` → `setup="idle"`;
  any exception → `setup="error"` + `error`. The Remote page polls status every 5 s to show progress.
- `start_agent_and_tunnels(secret)`: start agent if not running → `ensure_tunnel(server_port)` →
  `save_config(secret, address)`.
- Start: requires secret; runs `start_agent_and_tunnels` (skipped when the agent already runs);
  failure → console `[playit] Start failed: …` + 502.
- Field notes (2026-10-22, owner's Windows test + API probe with owner's consent): Minecraft tunnel
  creation/allocation works (account: 4 TCP ports allowed). The agent's IPv6 `NetworkUnreachable`
  ping errors are harmless (falls back to IPv4); on Windows `terminate()` makes the agent report
  exit code 1. **PlayIt cannot host the dashboard**: `/tunnels/create` without `tunnel_type` →
  `TunnelTypeRequiresDescription` (not in the published client's error enum; adding `description`
  doesn't help); `/v1/tunnels/create` returned "failed to parse body" for every body matching the
  published `ReqTunnelsCreateV1` (the server is ahead of github.com/playit-cloud/playit-agent
  master); the playit.gg website offered the owner no custom TCP tunnel. Hence §16 (Cloudflare).
- Stop: cancels a pending setup, stops agent, state `"stopped"`.
- Config: `GET /api/playit/config` → `{address, secret_present}` (secret never returned).
  `PUT /api/playit/config` `{secret?, address?}` (Remote → PlayIt → "Edit config (JSON)"): blank secret
  keeps the saved one; unknown keys → 400; no secret at all → 400; `connecting` → 409; cancels a
  pending approval (setup → `idle`, no error); a changed secret stops the agent. A cancelled claim now
  ends in `setup="idle"` instead of `"error"`.
- Agent exit → `_playit_on_exit` sets state stopped + console line.
- Leftover agents / restart loop (2026-10-22, seen on Termux: `playitd error: IPC error: Another
  instance is already running` → every new agent exits at once, and restart chains stacked up):
  `start_agent_and_tunnels` calls `playit.kill_stale_agents` before starting (processes whose
  cmdline/exe is our `_agent_path()`: `/proc` scan on POSIX, psutil on Windows); the agent runs in
  its own session/process group so `stop()` `killpg`s the termux-chroot/proot wrapper and the agent
  together. One pending restart at most (`runtime.playit_restart_timer`, cancelled by any start or
  deliberate stop); more than `PLAYIT_CRASH_LIMIT` (3) exits in `PLAYIT_CRASH_WINDOW_SEC` (600 s)
  → restarts paused, `setup="error"` with the agent's last error line, "DOWN" alert; Start tunnel
  re-arms. `ensure_agent` installs `proot` on Termux when `termux-chroot` is missing.
- Rejected key (2026-10-22, seen in the field: agent logs `ApiError(Auth(InvalidAgentKey))` forever
  while our own API calls with the same key still succeed and `/tunnels/create` keeps answering
  `AgentVersionTooOld`): `start_agent_and_tunnels` watches agent output via
  `playit.is_key_rejected` (`InvalidAgentKey` / `NoLongerValid`) → deliberate stop (no crash
  restart), `setup="error"` + `PLAYIT_KEY_REJECTED` ("Change account" to re-link), `tunnels` alert;
  `ensure_tunnel(should_abort=…)` stops waiting at once (also when the agent died). Agent output is
  stripped of ANSI colour codes. `PUT /api/playit/config` rejects non-hex secrets (the agent only
  accepts hex); secrets are trimmed before use, like the agent does.

Core details (`core/playit.py`):
- `claim_interactive(log, should_cancel, timeout_sec=300, on_url=None)`: random 10-hex claim code →
  `on_url(claim URL)` (no browser is opened; the user opens the link from any device) → polls
  `/claim/setup` every 1.5 s until `UserAccepted` → `/claim/exchange` for `secret_key`.
- `ensure_tunnel` → `_ensure_tunnel(..., matches, name, tunnel_type, label)`; `tunnel_type` is always
  sent (untyped tunnels are rejected).
- `agent_command(secret)`: on Termux, prefixes `termux-chroot` (package `proot`) so the agent finds
  `/etc/resolv.conf`; `EZ_PLAYIT_CHROOT=0` disables. Unverified on a device.
- `ensure_agent`: reuse existing binary, else download matching asset from GitHub latest release;
  `chmod 755` on non-Windows.
- `ensure_tunnel(secret, local_port, log, timeout_sec=120)`: reuses the account's `minecraft-java`
  tunnel; deletes/recreates it if `local_port` differs; retries create while API says
  `AgentVersionTooOld` (agent must be running first); polls `/tunnels/list` for the allocated address
  (`domain` if SRV, else `domain:port`).
- `_api` reads the JSON body before the HTTP status (API returns 400 with useful `data`); auth header
  `Authorization: Agent-Key <secret>`.
- `PlayitAgent.start(secret)` runs `agent_command(secret)`; `stop()` = `terminate()`.

## 16. Remote dashboard (Cloudflare quick tunnel)
- `GET /api/remote-dashboard` → `remote_info()`: `enabled` (config `remote_dashboard`), `state`
  (`off`/`starting`/`running`/`error`), `url`, `error`, `message`.
- `POST /api/remote-dashboard {enabled}`: saves the config key; enabled → `start_web_tunnel_safely`
  on a thread (returns immediately, state `starting`); disabled → `stop_web_tunnel`.
- `start_web_tunnel`: `cloudflared.ensure_binary` (PATH, else download
  `cloudflared-<os>-<arch>[.exe]` from GitHub latest release into `DATA_DIR`; on Termux it never
  downloads but runs `hostenv.termux_install("cloudflared")`) → `QuickTunnel.start(binary, WEB_PORT)` runs
  `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:<port>`.
- `QuickTunnel` announces the URL (`on_url`) only after both the `https://….trycloudflare.com` line
  **and** "Registered tunnel connection" were seen: announcing earlier lets browsers cache NXDOMAIN
  for minutes (observed in the live check). Public DNS may still lag a few seconds after that.
- Only cloudflared `ERR`/`WRN` lines reach the console (`[cloudflare] …`), plus the final link.
- Unexpected exit while wanted (`runtime.web_tunnel_wanted`) → state `error` + retry after
  `WEB_TUNNEL_RETRY_SEC` (15 s), indefinitely; the new link differs.
- Autostart restores it when `remote_dashboard` is on — **default True** since 2026-10-22 (owner
  decision), so it comes up on every boot unless switched off; shutdown stops it (config stays on).
  Each new link is emailed when alerts are configured (§17).
- Login through the link uses sessions + lockout (§1).

## 17. Alerts (email + heartbeat)
- Settings: `core/notifier.py`, file `EZ_Server_Data/notify_config.json` (keys `notifier.DEFAULTS` +
  internal `last_link`). `GET /api/notifications` → `public_config()` (no `smtp_password`, adds
  `password_set`); `PUT` merges (empty `smtp_password` keeps the saved one; `last_link` cannot be
  set from the API); `POST /api/notifications/test` sends a test email and/or pings the heartbeat
  (400 if neither is configured, 502 with the reason on failure). Server setup → Alerts card.
- `notify(category, subject, body, throttle=True)` (inside `create_app`): only when
  `email_ready()` and `notify_<category>` is on (`server` / `link` / `tunnels` / `updates` / `security`); same subject at most
  once per `ALERT_REPEAT_SEC` (600 s); sends on a daemon thread via SMTP (STARTTLS on 587, SSL on
  465); logs `[notify] email sent|failed`. Body adds host name, time, current dashboard link.
- Hooks: crash (`Minecraft server crashed`), limit reached (`… DOWN (auto-restart paused)`), restart
  failed / back online, unexpected exit with auto-restart off, autostart failures, new Cloudflare
  link (`link`, unthrottled, only when it differs from `last_link`), Cloudflare link down / failed
  to start, PlayIt agent stopped unexpectedly (`tunnels`).
- PlayIt agent crash (2026-10-22): `on_agent_exit` restarts `start_agent_and_tunnels` after
  `PLAYIT_RETRY_SEC` (30 s). Deliberate stops go through `stop_playit_agent()`, which clears
  `runtime.playit_agent` before `stop()`, so the exit isn't treated as a crash. `runtime.shutting_down`
  (set first in `shutdown()`) suppresses restarts and the heartbeat.
- Heartbeat: `heartbeat_loop` thread started by `autostart()`; GETs `heartbeat_url` every
  `HEARTBEAT_INTERVAL_SEC` (60 s). Meant for healthchecks.io-style dead-man's switches: covers
  outages the app can't report itself (device off, no internet, process killed).

## 18. Self-update (Server setup → Updates)
- `core/updater.py` works on `REPO_DIR` (= `paths.base_dir()`, the git clone the app runs from).
  `status(fetch)` → `available`/`reason` (no git or not a clone), `branch`, `upstream` (`@{u}` or
  `origin/<branch>`), `current` {hash, subject, when}, `latest_hash`, `behind`, `ahead`, `commits`
  (≤20 incoming), `local_changes` (tracked files only; porcelain parsed without stripping). Every `_git` call passes
  `-c core.fileMode=false`, so permission-only changes (old setup's `chmod +x run.sh ezmc.sh`) don't block.
- `apply(log)`: refuses on local changes or unpushed commits; else `git merge --ff-only <upstream>`
  (after the fetch in `status`); if `requirements.txt` changed → `install_requirements` (running
  interpreter's pip; Termux drops psutil and sets `ANDROID_API_LEVEL`) and on failure
  `git reset --hard <old>` (safe: no local edits by then).
- App: `POST /api/update/check` (fetch), `GET /api/update` (cached; first call reads without
  fetching), `POST /api/update/apply` → `run_update` thread: apply → write
  `EZ_Server_Data/resume_after_update.json` {server, playit} → `update_state="restarting"` → 1 s →
  `shutdown()` → `updater.restart_process()`. Failures → `update_state="error"` + `updates` alert.
- `restart_process()`: POSIX `os.execv(sys.executable, sys.orig_argv)` (same PID, so runit keeps
  supervising; listening sockets are non-inheritable and close on exec). Windows: spawns a helper that
  sleeps 3 s (old process releases the port) then runs the same command; old process `os._exit(0)`.
- `autostart()` consumes the resume marker and starts the server / PlayIt if they were running,
  even when their Always-on switches are off. It also starts `update_check_loop`: first check after
  `UPDATE_FIRST_CHECK_SEC` (120 s), then every 6 h when `auto_update_check` (default True); emails
  "Update available" once per upstream hash; if `auto_update_install` (default False) and no players
  online and no local changes → `run_update()`.
- Browser: `waitForRestart` polls `/api/update` every 2 s; when it fails (restarting) and then
  answers again, the page reloads. CSS/JS URLs carry `?v=ASSET_VERSION` (max mtime of the two files)
  so the reloaded page gets the new assets. The Server nav link shows a dot when updates are pending.

## 19. Worlds (Worlds page)
- `core/worlds.py`. A world = a folder in the server dir with `level.dat`, plus Paper's
  `<folder>_nether` / `<folder>_the_end` siblings. Active world = config `level_name` (default
  `world`) → `server.properties` `level-name` via `_server_properties_from_config` / `_active_world`.
  Display names, source and seed live in `<server dir>/ez-worlds.json`; rename never moves files.
- `list_worlds` = folders with `level.dat` (dimension siblings hidden) ∪ metadata entries (created
  but not generated yet, `exists: false`) ∪ the active folder; includes size and last played.
- Endpoints: `GET/POST /api/worlds` (create: name + optional seed), `POST /api/worlds/upload`
  (multipart `file` + `name`; streamed to `DATA_DIR/upload-*.zip` then `import_zip`), `POST
  /api/worlds/{folder}/activate` (if the server runs: **202** + lifecycle job "switching world":
  `stop_and_wait` → switch → `launch_server`; otherwise switches under the lock; for an ungenerated
  world also sets `level_seed` to its seed), `POST …/rename`, `DELETE …` (refused for
  the active world), `GET …/download` (zip, temp file removed after sending).
- `import_zip`: picks the shallowest `level.dat` that isn't a dimension folder (handles
  `saves/<name>/`, plain `<name>/` and root-level zips; ignores `__MACOSX`), imports matching
  `_nether`/`_the_end` siblings, extracts member-by-member with path checks (rejects `..`, absolute,
  drive letters) and removes partial output on failure.
- Backups follow the active world: `world-backup-<folder>-<ts>.zip` via `worlds.export_zip`
  (`save-all flush` first if running); restore accepts any members under valid world folder names
  and renames each existing folder to `<folder>-pre-restore-<ts>` before extracting.
- Worlds are per server dir (loader + version), like before.

## 20. Mod browser (Mods page)
- `GET /api/modrinth/search?query&sort&category&offset` → `{hits, total, offset, limit, loader,
  version}` (also `items` for compatibility). Empty query = browse; with sort `relevance` and no
  query it switches to `downloads`. Facets: project_type mod|plugin, **server_side required|optional**
  (client-only mods hidden), loader categories, game version, optional category. 20 per page.
  Vanilla → 400 with an explanation. Modrinth outages → 502.
- `GET /api/modrinth/categories` (from `/tag/category`, mod/plugin "categories" header, cached 1
  day); `GET /api/modrinth/project/{slug}` → details with `body` as plain text (`_plain_text` strips
  Markdown/HTML; the UI never renders remote HTML), gallery, links, license, compatible version count.
- `modrinth.install` installs the newest compatible version **and its required dependencies**
  (recursively, de-duplicated; optional ones skipped; a missing dependency only logs a warning) and
  records each in `<mods|plugins>/.ez-modrinth.json` (project id → slug/title/filename/version).
  `GET /api/server/mods` adds title/project_id/version per jar + `project_ids` (cards show
  "Installed"); `DELETE /api/server/mods/{name}` removes a jar (409 while the server runs, since the
  jars are in use) and its record entry.
- UI: toolbar (search, category, sort), card grid with icon/author/stats/chips/Install, "Load more",
  `<dialog id="mod-details">` filled with DOM nodes (textContent only).

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