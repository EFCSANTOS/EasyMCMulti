# Workflows

## 1. Authentication
`create_app()` → `validate_auth_settings()` raises `RuntimeError` if env vars missing.
REST: `require_auth` → `verify_credentials`. WS: `verify_basic_auth_header`; failure → `close(1008)`.
Browser: `/login` form → `GET /api/status` probe → creds in `sessionStorage` → every request sends
Basic header. Sign out clears `sessionStorage` only (no server session exists).

## 2. Dashboard
- `/` → `render_dashboard_page("overview")`
- `/dashboard` → 302 to `/dashboard/overview`
- `/dashboard/{section}` → page; unknown → 404
- `/dashboard.css`, `/dashboard.js` → `FileResponse` from `webapp/static/...`
- `/login` → `webapp/templates/login.html`
- Dashboard HTML embeds a hidden `#legacy-auth-hidden` form with `value="admin"` (see verification).

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
- Define `on_exit` (state→stopped, process=None, console line).
- `ensure_server_installed` → `update_properties` → `ensure_java` → `launch_args(loader, java, dir, ram)`.
- `ServerProcess(args, cwd, on_output=append_console_line, on_exit=on_exit).start()`.
- `state="running"`, `operation="idle"`, console `"[server] started with {loader} {version}"`.

## 6. Stop / restart / command
- Stop: if not running → message; else `state/operation="stopping"`, `process.stop()` (sends `"stop"`).
- Restart: `stop_server(_)` then `start_server(_)` (note: immediate, no wait for exit).
- Command: empty → 400; not running → 409; else `process.send(cmd)` + console `"> cmd"`.

## 7. Console
`GET /api/console` → `{lines: list(history)}`. WS replays last 500, then streams via queue.
`publish` trims, appends history, drops oldest when queue full, thread-safe.

## 8. Versions
`GET /api/versions/{loader}`: unknown loader → 400; `downloaders.list_versions(loader, include_snapshots=False)`;
on exception → `FALLBACK_VERSIONS[loader]`; empty → 502; else `{loader, versions}`.
Sources: vanilla releases (manifest order), paper (reversed API list), fabric stable game versions,
forge/neoforge MC versions derived from promos/Maven, sorted by `_version_key` desc.
NeoForge→MC mapping `_neoforge_to_mc`: `21.1.x`→`1.21.1`, `21.0.x`→`1.21`, major ≥25 → `<a>.<b>`.

## 9. Status
`GET /api/status`: refresh loader/version from config; `netinfo.lan_ip()` / `public_ip()`;
`playit.load_config()` → state `"configured"`/`"not-configured"`; address fallback to public IP;
returns `runtime.snapshot()`.

## 10. Metrics
Aliases `/api/system/metrics`, `/api/system`, `/api/stats`, `/api/metrics`, `/metrics` →
`{cpu, ram, ramTotal, uptime}` (psutil or Windows ctypes fallback).

## 11. Backups
- Create: zip `world/` → `world-backup-<ts>.zip`; missing world → `{ok:false}`.
- List: `GET /api/server/backups` → `{backups:[...]}`; legacy `GET /api/backups`.
- Restore: validate path exists; move current `world/` to `world-backup-pre-restore-<ts>`;
  `extractall(target_dir)`; legacy `POST /api/backups/restore`.

## 12. Mods
- List: `GET /api/server/mods` (legacy `GET /api/mods`) → `{mods:[{name,path}]}`.
- Upload: `POST /api/server/mods/upload` multipart; 400 if none; `.jar` only; 64 KiB chunks.
- Modrinth search: `GET /api/modrinth/search?query=` → `{items}` (limit 10); empty query → 400.
  Facets from `modrinth._LOADER_MAP` (paper → paper/spigot/bukkit/purpur; vanilla → no loader facet)
  plus `versions:<config version>`.
- Modrinth install: `POST /api/modrinth/install` with `slug`/`project_id`/`id`; target folder by loader;
  returns `{ok, filename}`.

## 13. Admin
`POST /api/admin/{action}`: not running → 409; unknown → 400; player required for op/deop/kick/ban;
maps to MC commands (`time set day`, `weather clear`, `save-all`, …); sends + logs `"> cmd"`.

## 14. PlayIt
- Status: `configured` = secret present; state logic prefers running agent, else config.
- Configure: if secret exists → "already configured"; else `claim_interactive` → `ensure_agent` →
  `PlayitAgent(...).start(secret)` → `ensure_tunnel(secret, server_port, ...)` → `save_config`.
- Start: requires secret; reuse running agent; else agent+tunnel; `save_config`; state `"running"`.
- Stop: stop agent, clear, state `"stopped"`.
- Config: `GET /api/playit/config` → `load_config()`.
- Agent exit → `_playit_on_exit` sets state stopped + console line.

Core details (`core/playit.py`):
- `claim_interactive`: random 10-hex claim code → `webbrowser.open(claim URL)` **on the server host,
  not the LAN client's browser** → polls `/claim/setup` every 1.5 s until `UserAccepted` →
  `/claim/exchange` for `secret_key`; 300 s timeout. The web app passes `should_cancel=lambda: False`,
  so `POST /api/playit/configure` blocks the request for up to ~5 min + tunnel wait.
- `ensure_agent`: reuse existing binary, else download matching asset from GitHub latest release;
  `chmod 755` on non-Windows.
- `ensure_tunnel(secret, local_port, log, timeout_sec=120)`: reuses the account's `minecraft-java`
  tunnel; deletes/recreates it if `local_port` differs; retries create while API says
  `AgentVersionTooOld` (agent must be running first); polls `/tunnels/list` for the allocated address
  (`domain` if SRV, else `domain:port`).
- `_api` reads the JSON body before the HTTP status (API returns 400 with useful `data`); auth header
  `Authorization: Agent-Key <secret>`.
- `PlayitAgent.start(secret)` runs `<agent> --secret <secret>`; `stop()` = `terminate()`.