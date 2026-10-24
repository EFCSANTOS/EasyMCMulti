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
- `/dashboard.css`, `/js/{name}` (names in `pages/layout.py::SCRIPTS`, else 404) → `FileResponse`
  from `webapp/static/...`; pages load the scripts in `SCRIPTS` order with `?v=ASSET_VERSION`
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
- Command: `validation.require_command` → 400 if empty, longer than 256 or containing a line break /
  control character (one stdin line, always); not running → 409; else `process.send(cmd)` + console
  `"> cmd"`.

## 7. Console
`GET /api/console[?since=<n>]` → `{lines, last}`: every publish increments
`WebSocketManager.last_seq`; with `since` only the lines after that sequence number
(`lines_since`). WS replays last 500, then streams via queue. `publish` trims, appends history,
drops oldest when queue full, thread-safe.
Browser (FEAT-11, `console.js`): `startConsole` loads the history, then opens `/ws/console` (session
cookie; `wss://` through the Cloudflare link) and appends each message (DOM capped at 2000 lines,
`textContent` only). If the socket closes or can't open: full reload, then poll `?since=<last>`
every `CONSOLE_POLL_MS` (2 s) and retry the socket every 30 s. Console page: filter Everything / Chat
only (`]: <Name> …` or `[chat] …`) / Warnings & errors, plus a search box. Command box: ↑/↓ recall
the last 50 commands (`localStorage` `ez_command_history`), Tab completes an online player's name.
Chat (overview): `POST /api/server/chat {message}` (one line, ≤ 200 chars, 409 when stopped) sends
`tellraw @a ["", {"text": "[Dashboard] "…}, {"text": "<user>: "…}, {"text": message}]` built with
`json.dumps`, and logs `[chat] <user>: <message>`.

## 8. Versions
`GET /api/versions/{loader}`: unknown loader → 400; `downloaders.list_versions(loader, include_snapshots=False)`;
on exception → `FALLBACK_VERSIONS[loader]`; empty → 502; else `{loader, versions}`.
Sources: vanilla releases (manifest order), paper (reversed API list), fabric stable game versions,
forge/neoforge MC versions derived from promos/Maven, sorted by `_version_key` desc.
NeoForge→MC mapping `_neoforge_to_mc`: `21.1.x`→`1.21.1`, `21.0.x`→`1.21`, major ≥25 → `<a>.<b>`.

## 9. Status
`GET /api/status` → `system.py::status_payload(config)`, a **pure read** (FIX-06): `runtime.snapshot()`
with loader/version from the config, `netinfo.lan_ip()` / `public_ip()` (cached 5 min), and
`playit.{state, address}` (`_playit_state` → `"running"` (agent alive) / `"configured"` /
`"not-configured"`; address falls back to the public IP) merged into the returned dict — nothing
is assigned to `runtime`. Loader/version are stored on `runtime` where they change (config save,
create, start).
Browser polls it every 5 s; displayed join addresses append the port from the loaded config.
The Remote page additionally calls `GET /api/playit/status` for the real tunnel address.

## 10. Metrics
Aliases `/api/system/metrics`, `/api/system`, `/api/stats`, `/api/metrics`, `/metrics` →
`{cpu, cpuScope, ram, ramTotal, temperatures, uptime}`. RAM: psutil → `/proc/meminfo` → Windows ctypes. CPU
(`common._cpu_usage(last)`, delta since the previous call with the same `last` dict; the dashboard
shares `_cpu_last`, the health check keeps its own): psutil `cpu_times` → `/proc/stat` (`cpuScope: "system"`) →
on Android, where apps can't read `/proc/stat`, the summed utime+stime of every readable
`/proc/<pid>/stat` (only Termux's own processes, the server included) over elapsed time × cores
(`"processes"`, shown as "now · Termux processes") → `0` / `"unavailable"`.
`temperatures`: `core/temperatures.read()` → `[{label, celsius, kind: "device"|"battery"}]`, hottest
first, from psutil sensors → `/sys/class/thermal/thermal_zone*` (m°C) + `/sys/class/power_supply/*/temp`
(0.1 °C) → `termux-battery-status` (Termux:API, cached 30 s); values outside 1–150 °C are dropped; `[]`
on Windows (needs admin). Overview "Temperature" card charts the hottest sensor and lists the others.

## 11. Backups (FEAT-01: schedule, retention, safety backups)
- Names (`core/backups.py`): `world-backup-<folder>-<YYYYmmdd-HHMMSS>[-<label>].zip`; labels
  `manual` (button), `auto` (schedule), `auto-pre-<reason>` (`update`, `delete`). Older names
  without a label (or without a folder) parse as `manual`.
- Create (`routes/backups.py::create_backup(label, folder=None, wait=False)`): one at a time
  (`backup_lock`; manual while one runs → 409); world not generated → `{ok:false}`; **low-disk guard**:
  free space < 2 × the last backup of that world → refused + `backup` alert. Server running (active
  world): `save-off` → `save-all flush` → wait for "Saved the game|world" (`wait_for_console`,
  `BACKUP_SAVE_TIMEOUT_SEC` 30 s; on timeout zip anyway with a console note) → zip → `save-on`.
  Server stopped: zip under the lifecycle lock (nobody can start it mid-zip). Then prune and log
  `[backup] created …; removed N old backup(s)`.
- Retention (`to_prune`, per world folder): keep the newest `backup_keep_recent` (8) plus the newest
  of each of the last `backup_keep_daily` (7) days; manual backups only when `backup_prune_manual`.
- Schedule: `webapp/scheduler.py::scheduler_loop` (started by autostart) calls `backup_tick` every
  `SCHEDULER_TICK_SEC` (60 s):
  off when `backup_interval_hours` is 0 (default 6); `runtime.backup_dirty` is set while the server
  runs and cleared by a backup made while it is stopped, so an unchanged world isn't backed up again;
  due when the newest backup of the active world is older than the interval.
- Safety backups: `s.safety_backup(reason, folder=None)` before deleting a world (failure → the
  delete is refused, 500) and before the self-update restart (failure → alert, update continues).
  Restore keeps its own safety copy (the folder rename below).
- Failures: console `[backup] … failed: …` + `notify("backup", "Backup failed", …)`.
- List: `GET /api/server/backups` → `{backups:[paths], items:[{name, path, folder, label, size,
  created, auto}], total_size, schedule}`; legacy `GET /api/backups`. Backups page: list with kind,
  world, size; total; "Automatic backups" card (interval, keep latest, keep per day, prune manual).
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
`POST /api/admin/{action}`: not running → 409; op/deop/kick/ban need a valid player
(`validation.require_player`: `^[A-Za-z0-9_]{3,16}$`, optionally prefixed with `.` for Floodgate
Bedrock names) → else 400; unknown action → 400;
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

## 17. Alerts (email + Discord + heartbeat)
- Settings: `core/notifier.py`, file `EZ_Server_Data/notify_config.json` (keys `notifier.DEFAULTS` +
  internal `last_link`). `GET /api/notifications` → `public_config()` (no secrets: adds
  `password_set`, `discord_owner_set`, `discord_friends_set`); `PUT` merges — secrets
  (`SECRET_KEYS`: SMTP password, both webhook URLs) keep the saved value when empty and are removed
  with `null`; webhook URLs must match `DISCORD_WEBHOOK` (else 400); `last_link` cannot be set from
  the API. `POST /api/notifications/test` sends a test email, posts to each configured webhook and/or
  pings the heartbeat (400 if nothing is configured, 502 with the reason on failure). Server setup →
  Alerts card.
- `notify(category, subject, body, throttle=True)` (`services.py`): when `notify_<category>` is on
  (`server` / `link` / `tunnels` / `updates` / `security`) and email is ready and/or the **owner**
  webhook is set; same subject at most once per `ALERT_REPEAT_SEC` (600 s); sends on a daemon thread
  via SMTP (STARTTLS on 587, SSL on 465) and `send_discord`; logs `[notify] email|Discord (owner)
  sent|failed`. Body adds host name, time, current dashboard link.
- **Friends** webhook (FEAT-05), `notify_friends(kind, text)` gated by `friends_notify_<kind>`:
  `status` — "Server is online: <Loader> <version> at <address>" after a start, "Server is offline."
  on a deliberate stop, crash / unexpected-stop messages; `players` — joins/leaves from
  `track_player_event` batched for `FRIENDS_BATCH_SEC` (30 s) into one "A, B joined · C left" (join
  and leave inside one batch cancel out); `link` — the new status page link (never the dashboard
  link). `send_discord` posts `{content (≤ 2000), allowed_mentions: {parse: []}}` (no @everyone from
  player names) and waits once on a 429 (`retry_after`, ≤ 10 s).
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
- Backups follow the active world: `world-backup-<folder>-<ts>-<label>.zip` via `worlds.export_zip`
  (quiet save first if running, §11); restore accepts any members under valid world folder names
  and renames each existing folder to `<folder>-pre-restore-<ts>` before extracting.
- Worlds are per server dir (loader + version), like before.
- Carrying player progress from one world to another: §37.

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

## 21. Players (Players page, FEAT-02)
- Lists are the server's own files in the server dir (`core/players.py::FILES`): `whitelist.json`,
  `ops.json`, `banned-players.json`, `banned-ips.json`; per server dir, like worlds.
- `GET /api/players/lists` → `{whitelist:[{name}], ops:[{name, level}], bans:[{name, reason}],
  ips:[{ip, reason}], recent:[{name, last_seen, rejected, whitelisted}], whitelist_enabled,
  online_mode, server_running, message}`.
- `POST /api/players/{whitelist|ops|bans|ips}` `{action, name|ip, level?, reason?}`; names through
  `require_player`, IPs through `ipaddress`, reason one line ≤ 100, level 1–4. Actions: whitelist
  add/remove/enable/disable, ops add/remove, bans add/remove, ips remove.
  - **Server running:** console command (`whitelist add`, `op`, `deop`, `ban <name> <reason>`,
    `pardon`, `pardon-ip`), then wait up to `SYNC_WAIT_SEC` (3 s) for the server to rewrite the file.
    `op` uses the server's default level (other levels only while stopped).
  - **Server stopped:** edit the JSON under the lifecycle lock (409 Busy otherwise) with
    `players.add/remove` (atomic writes). UUID: Mojang profile API in online mode (unknown name →
    400; Bedrock "." names refused while stopped), else the offline UUID (`offline_uuid`: MD5 of
    `OfflinePlayer:<name>` as UUID v3). Removing an unlisted name → 404.
  - `enable`/`disable`: saves `white_list` in the config (applied to `server.properties` on the next
    start) and, if running, sends `whitelist on|off`.
- Recently seen: `RuntimeState.recent_players` (in memory, newest 20): joins (`track_player_event`)
  and whitelist refusals (`players.rejected_name`, three log formats anchored to the line prefix so
  chat can't fake them) from the server output.
- UI (`static/js/players.js`): whitelist switch + warning when off, add forms (native `pattern`
  matches `require_player`), rows with Remove / Unban / "Add to whitelist" (textContent only).

## 22. Invite friends + public status page (FEAT-03)
- "Invite friends" card (`pages/sections_invite.py`) on Overview and Remote access:
  `GET /api/status-page` (auth) → `{enabled, show_names, show_lan, url, url_is_lan_only, invite}`;
  "Copy invite" copies `invite` (address, `<Loader> <version>`, "(mods required…)" when a
  Fabric/Forge/NeoForge server has jars, status page link when on).
- `POST /api/status-page {action}`: `enable` (creates `status_page_token` = `secrets.token_urlsafe(16)`
  if none), `disable`, `regenerate` (new token; old link 404s at once), `settings {show_names?, show_lan?}`.
  The token is never accepted from `PUT /api/config` (not in `ConfigUpdate`).
- Public, **no auth**: `GET /s/{token}` → `templates/status.html` (`Cache-Control: no-store`,
  `X-Robots-Tag: noindex`), which polls `GET /s/{token}/status.json` every 30 s. Disabled, empty or
  wrong token (`secrets.compare_digest`) → 404. JSON: `name` (MOTD), `online`, `state`,
  `players_online`, `players_max`, `address`, `version`, `loader`, `mods_required`, `modpack_url`
  (FEAT-04), plus `player_names` if `status_page_show_names`, `lan_address` if `status_page_show_lan`.
- Address: the PlayIt address if configured, else `<public IP>[:port]` (omits `:25565`), else empty.
- Link base: the Cloudflare link when it is up, else `http://<LAN IP>:<WEB_PORT>` (LAN-only, flagged
  by `url_is_lan_only`). The "New dashboard link" email includes the new status page link.

## 23. Mod pack for friends (FEAT-04)
- `core/modpack.py::build(sdir, loader, mc_version, name)` → `<sdir>/ez-modpack.mrpack` + summary
  `<sdir>/ez-modpack.json`; only for Fabric/Forge/NeoForge with jars in the mods folder (else
  `ValueError` → 400; Paper/vanilla invites say "no mods needed").
- Every jar is hashed (SHA-1 + SHA-512); one `POST /v2/version_files` (by SHA-1) + one
  `GET /v2/projects?ids=` give the CDN URL and `client_side`/`server_side`. Known + client not
  "unsupported" → `files[]` entry (`path` `mods/<jar>`, hashes, `env`, `downloads`, `fileSize`);
  client "unsupported" → left out (`excluded`); unknown jar → bundled under `overrides/mods/` with a
  licence warning. Modrinth unreachable → everything bundled + warning.
- `dependencies`: `minecraft` + `fabric-loader`/`forge`/`neoforge` from `loader_version()`
  (installed `libraries/` folders; Fabric before first start: `server.jar` → `install.properties`).
  Not found → omitted + warning "start the server once".
- Cached by `fingerprint` (loader, version, name, each jar's name/size/mtime): rebuilt only on
  change; one build at a time (`modpack_lock` in `routes/mods.py`).
- Owner: Mods page card "Mod pack for friends" (shown when `GET /api/modpack` → `available`):
  `POST /api/modpack/build` → summary toast (+ warnings), then `GET /api/modpack/download`.
- Friends: status page shows "Download the mod pack" (`/s/<token>/modpack.mrpack`, no auth, 404 when
  the token is wrong or there is nothing to pack); the invite says to get it there.
- Second option, single jars (no zip), for launchers without .mrpack import: the summary lists
  `client_jars` (every jar in the pack, Modrinth or bundled; server-only ones left out; older cached
  summaries without it are rebuilt once). Owner: "Download jars" → `GET /api/modpack/jars`
  `{jars: [{name, size}], excluded, warnings}`, then one `GET /api/modpack/jars/<name>` per jar, 400 ms
  apart (browsers drop simultaneous downloads), and the links stay listed in the card. Friends: "Download
  the mod files (.jar)" → `/s/<token>/mods.json` + `/s/<token>/mods/<name>`. A name outside
  `client_jars` → 404 (`modpack.client_jar_path`), so server-only mods and path tricks can't be fetched.

## 24. Schedule: daily restart, idle stop, quiet hours (FEAT-06)
- `webapp/scheduler.py`: one loop, one tick (`scheduler_tick(now)`): backups (§11), then the
  daily restart, then the idle stop. Server setup → Schedule card (config keys below; saved with the
  other setup fields through `CONFIG_FIELDS`).
- **Daily restart** at `restart_daily_time` ("HH:MM" host local time, "" = off), only while running:
  within 10 minutes of the time, if someone is online, `say Server restarting in N minute(s).` at
  10, 5 and 1 minutes (N is the real time left, so a late joiner hears "3", not "5");
  `runtime.restart_warnings_sent` prevents repeats. At the time (up to `RESTART_LATE_LIMIT` = 30 min
  late, not later): `say … now`, `safety_backup("restart")`, then the normal restart job
  (`start_lifecycle_job("restarting", restart_job)`); `runtime.daily_restart_done` = today's date
  (cleared again if the lifecycle lock is busy, so the next tick retries); friends get "Daily
  restart…".
- **Idle stop** after `idle_stop_minutes` (0 = off) with nobody online: `runtime.empty_since` is set
  when the server starts and when the last player leaves, cleared by a join. Due → lifecycle lock
  (non-blocking; skip this tick if busy) → `stopped_reason = "idle"` → `request_stop()` (deliberate,
  so not a crash) → console line and one friends message with the reason (the generic "Server is
  offline." is suppressed). `/api/status` returns `stopped_reason`; the pill reads "Stopped · idle".
  A start clears it.
- **Quiet hours** `quiet_hours_start`–`quiet_hours_end` (may wrap midnight; "" = off,
  `common._in_quiet_hours`): `launch_when_free` skips `autostart` and `crash restart` starts in that
  window (console `[schedule] quiet hours: … skipped`). Manual starts and the daily restart still work.

## 25. Crash diagnosis and log files (FEAT-08)
- `core/diagnose.py::diagnose(lines, crash_report)` — pure rule table, most specific first; items
  `{code, title, message, action}`: `missing_dependency` (Fabric "Mod 'X' (x) … requires … of mod
  'Y' (y), which is missing" and the bare-id form; Forge/NeoForge "Mod ID: 'y', Requested by: 'x', …
  Actual version: '[MISSING]'" or a wrong version) → `install_mod {slug}`; `incompatible_mods`;
  `client_only_mod` ("invalid dist DEDICATED_SERVER" / Fabric environment) → `remove_mod {file}` when
  the jar is named; `wrong_java` (class file version → Java = version − 44); `out_of_memory` →
  `open_setting setup/ram-input`; `port_in_use` → `kill_process {port}`; `world_locked`; `eula` →
  `accept_eula`. `first_line(items)` = "Likely cause: …" for alerts.
- `routes/diagnosis.py::run_diagnosis()` runs on every unexpected exit (in `on_exit`, before the
  crash handling) over the last `DIAGNOSIS_LINES` (300) console lines + the newest
  `crash-reports/*.txt` written since `runtime.last_start_at`; stores `runtime.diagnosis` /
  `diagnosis_at`, logs `[diagnosis] …` lines; the crash / DOWN / stopped-unexpectedly alerts
  append `first_line`. A start clears it. `GET /api/server/diagnosis` → `{items, at}`;
  `/api/status` carries `diagnosis_at` so the dashboard reloads the card when it changes.
- `POST /api/server/diagnosis/fix {kind}` (409 while running): `accept_eula` writes eula.txt;
  `kill_process {port}` finds the listening PID with psutil and terminates it **only if the
  process name contains "java"** (else 409 naming it; 404 if nothing listens). Install/remove use
  the existing Modrinth install and mod delete routes.
- UI: "Why the server stopped" card (Overview, Console) with one button per action (kill asks
  first); Console → "Log files" lists `logs/*.log|.log.gz` and `crash-reports/*.txt` (name pattern
  `^[\w.-]+\.(log|txt|log.gz)$`, folder must be one of the two) with View (last 2000 lines,
  gz decompressed: `GET /api/server/logs/{folder}/{name}`) and Download (`?download=true`).

## 26. Mod and plugin updates (FEAT-07)
- `core/mod_updates.py::check(dest_dir, loader, mc_version)`: SHA-1 of every jar → **one**
  `POST /v2/version_files/update {hashes, algorithm: sha1, loaders, game_versions}`; a jar is
  outdated when the newest version's primary file has another hash. Works for uploaded jars Modrinth
  recognises. Items: file, title (record title or file name), current, latest, changelog (plain
  text, 400 chars), new_file, url, hashes, required dependencies. Stored in `runtime.mod_updates`.
- `update()`: download to `<new>.download`, verify SHA-1 and SHA-512 (mismatch → error, old jar
  untouched), rename the old jar to `<file>.old` (Minecraft only loads `.jar`), move the new one in,
  record `filename`/`version` plus `previous`/`previous_version` (one generation: a second update
  deletes the older `.old`), install missing required dependencies.
- Routes (`routes/mods.py`): `GET /api/mods/updates`, `POST /api/mods/updates/check` (400 vanilla,
  502 Modrinth down), `POST /api/mods/updates/apply {files?}` (409 while running; safety backup
  `auto-pre-mod-update` **before** taking the lifecycle lock, since the backup takes it itself; per-mod
  failures reported, the rest go on; `pack_changed` → "friends should download the pack again"),
  `POST /api/mods/updates/rollback` (409 while running; restores every `.old`, removes the new jar,
  fixes the record).
- `scheduler.py::old_mods_tick`: once the server has run `OLD_MODS_KEEP_SEC` (300 s) since its last
  start, `forget_old()` deletes the `.old` jars.
- Daily: the self-update loop also runs the mod check and alerts (`updates`) once per new set;
  updates are never installed unattended.
- UI: Mods page → Updates card (Check, per-mod Update with old → new and changelog, Update all,
  Undo last update).

## 27. Player activity and playtime (FEAT-12)
- `core/activity.py`: `record(event, name, server_dir)` appends `{t, event, name, server_dir}` to
  `EZ_Server_Data/activity.jsonl` — `join` / `leave` from the server output (`on_output`), and
  `server_stop` from `on_exit` (stop or crash) and at every dashboard start (autostart: the
  dashboard owns the server, so nobody can still be online), which closes open sessions.
- `summary(now, days=30)`: replays the file (sorted by `t`; a half-written last line is skipped):
  per player `playtime_seconds`, `sessions` (a second join without a leave continues the session),
  `first_seen`, `last_seen`, `online`; plus `hours[24]` = average players online per hour of day over
  the last 30 days. `compact()` (dashboard start) folds everything before the last `server_stop`
  older than a year into per-player `totals` lines.
- `GET /api/players/activity` → summary. Players page → Activity card: SVG bar chart (24 bars,
  `--series-1`, 4px rounded tops, 2px gaps, hover tooltip kept inside the card) and per-player rows
  (Online badge, playtime, sessions, last/first seen).

## 28. Performance presets and JVM flags (FEAT-13)
- `downloaders.jvm_flags(ram_mb, is_phone)`: Aikar's G1 flags (without the obsolete
  `G1RSetUpdatingPauseTimePercent`); > 12 GB heap → the large-heap sizing (16M regions);
  `-XX:+AlwaysPreTouch` only off phones. `launch_args(..., extra_flags)` puts them after `-Xms/-Xmx`.
- `launch_server`: flags when `jvm_preset_flags` (default True), then `shlex.split(jvm_extra_args)`.
  `jvm_extra_args` validation: one line, ≤ 500 chars, only `-X…`/`-D…` tokens (no `-jar`, `@argfile`
  or program names) → else 422.
- `GET /api/system/host` → `{ram_mb, phone}`. Server setup → Performance card: presets Phone (2 GB,
  view 6, simulation 4), Balanced (3 GB, 10/8), PC with many mods (6 GB, 10/8), each capped at the
  device's safe maximum (≈50 % of RAM on a phone, ≈70 % on a PC, in 256 MB steps) and saved with
  `PUT /api/config`; RAM advice under the memory input ("Recommended: min–max", red above the max;
  modded loaders recommend ≥ 3 GB); the flags switch; extra options; a Chunky tip on Paper.

## 29. Server icon, MOTD editor, all properties (FEAT-14)
- Icon: the browser crops any picture to a centred square and draws it on a 64×64 `<canvas>`
  (`identity.js::iconBlob`), then `POST /api/server/icon` (multipart). The server checks the PNG
  signature, `IHDR` = 64×64 and size < 64 KB (else 400) and writes `server-icon.png` atomically
  (`fsutil.write_bytes_atomic`); `GET` serves it (404 if none), `DELETE` removes it. No imaging
  library on the server.
- MOTD (Settings page): colour swatches and Bold/Italic/Reset insert `§` codes, "New line" inserts
  the two characters `\n`; a preview styled like the multiplayer list renders them; the length line
  counts visible characters. `motd` may be 150 chars. Non-ASCII characters (the `§`) are written to
  server.properties as `\uXXXX` escapes (`properties_meta.escape_value`), which Java's properties
  loader reads whatever the file encoding; the owner's `\n` stays a properties escape.
- All properties (Settings page): `GET /api/server/properties` → one row per key in the file, in
  `properties_meta.KNOWN` (type, description, options for ~45 common keys), in `extra_properties` or
  managed; managed keys (`properties_meta.MANAGED`, written by the Settings form on every start) and
  `LOCKED` keys (port, level-name, white-list, RCON) are read-only. `PUT {key: value|null}` merges
  into config `extra_properties` (validated: key pattern, not managed/locked, one line ≤ 500 chars;
  null removes) → `_server_properties_from_config` writes them on the next start, managed keys
  winning.

## 30. Storage use and cleanup (FEAT-15)
- `core/storage.py::usage(active_dir)` (cached 5 min; `?fresh=true` re-measures) walks the data
  folder with `os.scandir`: per server dir its own size (active ↔ "not in use; last used <date>"
  from `logs/latest.log`/`server.properties` mtime), its backups (info, managed on the Backups page),
  `*-pre-restore-*` folders, `installer.jar`, `logs/*.log.gz` older than 30 days, each `java/jre-N`
  (deletable when no server dir's version needs Java N), and top-level files (tools, settings).
  `disk()` = `shutil.disk_usage` + `low` (< 2 GB or < 10 % free).
- `POST /api/storage/delete {id}`: 409 while running or busy; the id is looked up in a fresh scan
  (unknown → 404, not deletable — e.g. the active server dir or backups → 409), so clients name items,
  never paths.
- Scheduler `storage_tick`: low space → one `backup`-category alert ("Storage almost full") until
  space recovers. Backups page → Storage card (free/total, list, Delete with a confirm that says what
  is lost).

## 31. Move to another version (FEAT-09)
- `core/migrate.py::plan(old_sdir, old_loader, old_version, new_loader, new_version, new_sdir)`:
  `downgrade` (`_version_key` compare), `loader_change`, `worlds` (every generated world folder +
  dimension folders), `files` (`ez-worlds.json`, whitelist/ops/banned lists, `server-icon.png`),
  `mods` — same modded loader: one `POST /v2/version_files/update` for the target version →
  `compatible` (download info) / `missing`; Paper → plugins `carried` as they are; loader change →
  nothing carries over — `replaces` (folders already in the target dir), `size`, `free`,
  `enough_space` (> 1.1 × size + 200 MB).
- `apply()`: copies (never moves) the worlds and files; target folders that already exist are
  renamed `<folder>-pre-migrate-<ts>` first; downloads compatible mods (SHA-1 checked) and records
  them; copies carried plugins.
- Routes (`routes/migrate.py`): `POST /api/server/migrate/plan {loader, version}` (400 same
  version, 502 Modrinth down); `POST /api/server/migrate {loader, version, skip_missing?,
  allow_downgrade?}` → 409 while running, for a downgrade without `allow_downgrade`, without enough
  space, or with missing mods without `skip_missing`; else **202** lifecycle job "migrating":
  `backups.create(old, active, "auto-pre-migrate")` (core call: the job already holds the lock) →
  `ensure_server_installed(new)` → `apply` → save config (loader/version) → message about the slow
  first start (or, for a downgrade, that the world may not load).
- UI: Server setup → "Move to another version" (loader, version list from `/api/versions`, Check →
  plan list with warnings, Move → confirm → background job with the usual progress/toast).

## 32. Friend accounts (FEAT-10)
- Roles: **owner** = the `EZ_WEBAPP_USERNAME`/`PASSWORD` account (never stored, can't be locked out
  by the users file); **friend** = accounts in `EZ_Server_Data/users.json`
  (`core/users.py`: `{username: {role, salt, hash, created}}`, `hashlib.scrypt` n=2^14 r=8 p=1,
  `hmac.compare_digest`; unknown names still pay one scrypt so timing doesn't reveal them).
- `auth.login_role()` → "owner" / "friend" / None; `check_login` returns the role; sessions store
  `(username, role)`; `authenticate()` returns `auth.User` — a `str` (the username) with `.role`
  and `.is_owner`.
- Dependencies (`services.py`): `require_auth` = **owner only** (403 "Only the server owner can do
  that."), used by every route unless opened on purpose; `require_user` = any role, used only by
  `GET /api/session`, `GET /api/status`, the metrics routes, `POST /api/server/start` (a friend's
  payload is ignored: they can't change settings), `POST /api/server/chat`,
  `POST /api/server/backup`, and stop/restart through `require_can_stop` (owner, or a friend when
  config `friends_can_stop` is on). `/ws/console` is owner-only. Friend actions log
  `[dashboard] <name> started the server` etc.
- Owner routes (`routes/users.py`): `GET /api/users`, `POST /api/users {username}` (2–32 chars
  `[A-Za-z0-9_.-]`, not the owner's name, unique) → generated 12-char password shown once,
  `POST /api/users/{name}/reset` → new password + `sessions.delete_user`, `DELETE /api/users/{name}`
  → removed + signed out everywhere.
- UI: `/api/session` → `{username, role, can_stop}`. Friends: `bootFriend()` — only the overview
  (other pages redirect), `body.is-friend` hides every `[data-owner-only]` element (nav items except
  Overview, invite card, diagnosis, console output and command box) and, without `can_stop`,
  `[data-stop-control]` (Stop/Restart); only status, metrics, chat and backup load. Owner: Friends
  page (create, new password, remove, "Friends can stop and restart" switch).

## 33. Installable app (FEAT-16)
- `GET /manifest.webmanifest` (public, `application/manifest+json`): name, short name "EZ MC",
  `display: standalone`, `start_url`/`id` `/dashboard/overview`, theme/background `#0d0e10`, icons
  192/512 (`any`) and 512 `maskable`. `GET /icons/{name}` serves only the four PNGs in
  `static/icons/` (grass block drawn by a stdlib script; regenerate rather than hand-edit).
- Every page and `/login` link the manifest, `theme-color` and the apple-touch-icon. No service
  worker: current Chrome/Edge don't need one to install, and caching would only risk stale state.
- Installing needs https (the Cloudflare link) or localhost; the Remote access page says so.

## 34. Own-domain dashboard link (FEAT-17)
- `remote_mode`: `quick` (default: `QuickTunnel`, random trycloudflare.com link) or `named`.
  Named is used only when `remote_hostname` is set **and** a token is saved
  (`remote.py::named_mode`): `cloudflared.NamedTunnel(hostname)` runs
  `cloudflared tunnel --no-autoupdate run` with the token in `TUNNEL_TOKEN` (not on the command line)
  and reports `https://<hostname>` once "Registered tunnel connection" appears — the same address
  after every restart. The hostname is set up in the Cloudflare dashboard; this app never calls the
  Cloudflare API.
- Token: `EZ_Server_Data/cloudflare_tunnel.json` (atomic; `TOKEN_PATTERN`), never returned —
  `GET /api/remote-dashboard` adds `mode`, `hostname`, `token_set`.
- `POST /api/remote-dashboard/named {mode, hostname?, token?}`: hostname normalised (scheme and
  trailing slash removed, lower case) and validated (422); empty token keeps the saved one, `null`
  forgets it; malformed token → 400; named without hostname+token → 400; restarts the link when on.
- UI: Remote access → "Use your own domain" (steps, link type, hostname, token). Tailscale/ZeroTier
  is documented in the README only.

## 35. Bedrock crossplay (FEAT-18)
- `core/geyser.py` (Paper, Fabric, NeoForge): `install()` — Geyser from Modrinth (with required
  dependencies, e.g. Fabric API); Floodgate from Modrinth on Fabric/NeoForge but from GeyserMC's
  download server on Paper (`FLOODGATE_SPIGOT_URL` → `plugins/floodgate-spigot.jar`; Modrinth has
  no Paper build). `uninstall()` removes just those two (dependencies stay). `configure(sdir,
  loader, port)` edits Geyser's `config.yml` line by line: `bedrock: port` and `auth-type: floodgate`
  under `remote:` (older) or `java:` (newer); idempotent; False until Geyser has written the file on
  its first start. `launch_server` calls it on every start when `bedrock_enabled`.
- PlayIt: `ensure_bedrock_tunnel()` = `_ensure_tunnel(..., "minecraft-bedrock", port_type="udp")`
  → `domain:port`; saved as `bedrock_address` in `playit_config.json` (`save_config` keeps it unless
  the key changes). `start_agent_and_tunnels` ensures it after the Java tunnel when crossplay is on;
  a failure only sets `runtime.bedrock_error`. **The Bedrock tunnel type hasn't been tried against a
  real PlayIt account yet.**
- `GET /api/bedrock` → `{supported, loader, enabled, busy, port, address, configured, error}` (read
  only); `POST /api/bedrock {enabled}`: on → 400 for vanilla/Forge, else background install
  (`bedrock_busy`) → `bedrock_enabled` + tunnel if the agent runs; off → uninstall + disabled.
- Bedrock address: Remote access card, invite text ("Bedrock (phone/console): …") and the public
  status page. Whitelist form "Bedrock player" adds Floodgate's `.` prefix (spaces → `_`).

## 36. Device health alerts (CPU / RAM / temperature)
- `webapp/routes/health.py::health_tick`, run by the scheduler once a minute (after `storage_tick`):
  CPU = average since the previous tick (own `_cpu_usage` dict), RAM % = used / total, temperatures =
  hottest "device" and hottest "battery" reading.
- Usage (`notify_usage`): CPU ≥ `alert_cpu_percent` or RAM ≥ `alert_ram_percent` for
  `alert_usage_minutes` consecutive ticks → one alert. Temperature (`notify_temperature`): device ≥
  `alert_temp_c` or battery ≥ `alert_battery_c` for 2 ticks in a row. Each watch alerts once, then
  re-arms only when the value drops `RECOVER_MARGIN` (5) below the limit, which also sends "… is
  back to normal". Not throttled (the state machine already prevents repeats).
- Where they go: `channels_usage` / `channels_temperature` = "both" | "email" | "discord";
  `notifier.channels(config, category)` filters inside `services.notify` (other categories: both).
- Settings: Server setup → Alerts → "Device health" (switch + "Send by" + limits per alert), saved via
  `PUT /api/notifications`; `notifier.save_config` rejects other channel values and limits outside
  `notifier.LIMITS` (400). Termux setup installs `termux-api`; the battery reading also needs the
  Termux:API app.

## 37. Carry player progress between worlds
- For starting a fresh world but keeping what players earned. `core/player_transfer.py`, card
  "Carry player progress" on the Worlds page; the old world is only read.
- What moves, per player (files named by UUID in the source world): `playerdata/<uuid>.dat`
  (inventory, ender chest, XP, health, effects and whatever mods attach to the player: Forge
  `ForgeCaps`, NeoForge attachments, Fabric cardinal components, so a worn Traveler's Backpack,
  Curios / Trinkets slots), other `playerdata/<uuid>.<ext>` files some Forge mods write (not
  `.dat_old`), `stats/<uuid>.json`, `advancements/<uuid>.json`. Each kind is a switch.
- Mod data kept in the world rather than on the player (e.g. `data/sophisticatedbackpacks.dat`,
  `ftbquests/`): `mod_data()` offers every non-vanilla top-level entry and every `data/` entry
  that isn't vanilla (`_VANILLA_ENTRIES`, `_VANILLA_DATA`: raids, maps, idcounts, structures…);
  unticked by default; copied over same-named entries. Only listed paths are accepted.
- Both worlds must be generated (`level.dat`), so it runs after the new world's first start.
  "Send everyone to the new spawn" (default on) rewrites the player NBT via `core/nbt.py`: `Pos` =
  the target's spawn (`Data.SpawnX/Y/Z`, or the 1.21.9+ `Data.spawn.pos`), `Dimension` = its
  dimension (int 0 before 1.16), and drops `_LOCATION_TAGS` (motion, vehicle, bed/anchor respawn,
  ender pearls, Bukkit/Paper world UUID). Off: player files are copied as they are.
- `prepare()` validates and reads everything (bad UUID, unknown mod data, damaged player file → 400)
  before anything is written; `apply()` writes. `transfer()` = both.
- Endpoints: `GET /api/worlds/{folder}/players` → `{players: [{uuid, name, parts, last_seen}],
  mod_data: [{path, size}]}` (names from whitelist, ops and `usercache.json`); `POST
  /api/worlds/{target}/transfer` `{source, players (null = all), parts, mod_data, to_spawn}`: 409
  while the server runs (it rewrites player files), lifecycle lock (409 busy), then `prepare` →
  `backups.create(…, "auto-pre-transfer")` of the target → `apply`. Synchronous: only player files
  and the chosen mod data are copied.
- `core/nbt.py`: dependency-free NBT codec (gzip or raw), Java modified UTF-8 (null as `C0 80`,
  characters above U+FFFF as surrogate pairs), atomic `save`. Unchanged data round-trips
  byte-identical (checked on a real 1.21.7 `level.dat`).

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