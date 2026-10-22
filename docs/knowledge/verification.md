# Verification & Gaps

## Must fix (next commits)
**Login is LAN-grade but can now be reached from the internet.** The optional remote dashboard
(`POST /api/remote-dashboard`, Cloudflare quick tunnel, 2026-10-22) is HTTPS, so the password is
encrypted in transit, but:
- no lockout or rate limit on wrong passwords — anyone with the link can brute-force;
- Basic auth re-sends the password on every request, and the browser keeps it in `sessionStorage`;
- Cloudflare sees traffic in clear at its edge (TLS terminates there).

Owner decision 2026-10-22: ship remote access with the current login, fix in an upcoming commit.
**Priority raised:** since 2026-10-22 the link is **on by default** (`remote_dashboard` default
True), so every install is reachable from the internet unless the owner switches it off.
Required fix: server-side login sessions (HttpOnly, Secure, SameSite cookie, expiry), failed-login
lockout / backoff (per client IP from `CF-Connecting-IP` when behind the tunnel), minimum password
length enforced at startup when remote access is enabled, and stop storing the password
client-side. Mitigations shipped meanwhile: confirm dialog + callout on the Remote page, random
unlisted link that changes on every restart, `termux-setup.sh` requires a 12+ character password. Marked in code at
`webapp/auth.py` ("MUST FIX").

## Fetch status
Audited 2026-10-22: all of `core/`, `webapp/` (incl. `static/js/dashboard.js`, `templates/*.html`),
`tests/`, `requirements.txt`, `run.bat`, `.env.example`, `.gitignore`, README. Smoke suite: 67 passed.
UI redesign (2026-10-22) verified with headless Edge screenshots at 1440 px and 390 px.
Same day: cleanup + English-only pass + GUI feature check (see "Fixed 2026-10-22").
Not audited line-by-line: `webapp/static/css/dashboard.css` (presentation only).
GUI verified by: smoke tests, a live uvicorn boot hitting every page and the GUI's GET APIs, and
`node --check` on `dashboard.js`. Not verified in a real browser session or with a real Minecraft
server / PlayIt agent.

## Verified route table
UI: `/`, `/login`, `/dashboard`, `/dashboard/{section}`, `/dashboard.css`, `/dashboard.js`.
Health: `/health`.
Metrics aliases: `/api/system/metrics`, `/api/system`, `/api/stats`, `/api/metrics`, `/metrics`.
Status/console: `/api/status`, `/api/console`.
Config: `GET/PUT /api/config`.
Versions: `/api/versions/{loader}`.
Server: `POST /api/server/{create,start,stop,restart,command,backup}`.
Backups: `GET /api/server/backups`, `POST /api/server/backups/restore`, legacy `GET /api/backups`,
`POST /api/backups/restore`.
Mods: `GET /api/server/mods`, `POST /api/server/mods/upload`, legacy `GET /api/mods`.
Admin: `POST /api/admin/{action}`.
Modrinth: `GET /api/modrinth/search`, `POST /api/modrinth/install`.
PlayIt: `GET /api/playit/status`, `POST /api/playit/{configure,start,stop}`, `GET /api/playit/config`.
Remote dashboard: `GET/POST /api/remote-dashboard`.
WS: `/ws/console` (auth via `Authorization: Basic`; close 1008 on failure).

## GUI feature status (dashboard ↔ API)
| Feature | Status |
|---|---|
| Login / sign out | Works (sessionStorage Basic auth) |
| Loader + version pickers | Works — versions fetched from `/api/versions/{loader}`, refreshed on loader change |
| Save config (Setup and Advanced pages) | Works — saves only the fields present on the page |
| Create / start / stop / restart server | Works (restart waits for exit; see below) |
| Console | Works — polled every 2 s via `GET /api/console` |
| Console commands, admin buttons | Works (409 when server not running) |
| Backup / list / restore | Works |
| Mod search / install (Modrinth), upload jars | Works; vanilla → 400 "does not support mods" |
| PlayIt configure / start / stop / status | Works; configure runs in the background and the dashboard shows the approval link (open from any device); "Change account" resets |
| Remote dashboard (Cloudflare) | Works: live-checked on Windows 2026-10-22 (download, registration-gated link, request served through `trycloudflare.com`); login caveat — see Must fix |
| ~~Dashboard over PlayIt~~ | Removed: PlayIt's `/tunnels/create` rejects untyped tunnels (`TunnelTypeRequiresDescription`); `/v1/tunnels/create` rejected every body that matches the published client ("failed to parse body"); the website did not let the owner add a custom TCP tunnel either |
| System metrics charts | Works (overview only) |
| Player count + names | Works — parsed from server join/leave console lines |
| Always-on (autostart server/PlayIt, crash restart) | Works in tests and a live uvicorn boot; not yet run on a device |
| Self-update (Updates card) | Git logic tested against real temp repos and checked read-only on the real clone; **the process restart itself (exec on Linux/Termux, relaunch helper on Windows) is not yet run for real** |
| Alerts (email, heartbeat) + PlayIt agent auto-restart | Works in tests with stubbed SMTP/HTTP; **real Gmail send and healthchecks.io ping not yet tried** (needs the owner's credentials) |

## Fixed 2026-10-22
- Mod upload posted to non-existent `/api/mods/upload` → now `/api/server/mods/upload`.
- Settings `whitelist`/`command_blocks`/`seed` sent under wrong keys → now `white_list`,
  `enable_command_block`, `level_seed` (load and save).
- Save crashed on `/dashboard/setup` (unguarded advanced inputs); `/dashboard/advanced` had no save
  button → `collectConfigFromPage` + `#advanced-save-button`.
- Version dropdown never populated (JS never called `/api/versions`).
- Console fetched once at boot, never refreshed → polled.
- Action feedback only visible on overview → `#action-status` bar on every page.
- PlayIt panel never updated (JS read `status.playit_state`; API nests `status.playit.state`).
- `/api/playit/status` and `/api/status` claimed `"running"`/`"configured"` regardless of the agent
  process → shared `_playit_state` checks the live agent.
- Status summary showed `Port: n/a` (no `server_port` in snapshot) → read from loaded config.
- **Restart**: called `start_server(_)` passing the username as `payload` (→ `dict.update("admin")`
  500 when stopped) and did not wait for exit. Now waits up to `RESTART_STOP_TIMEOUT_SEC` (60 s),
  kills on timeout, then `start_server(None, _)`. Old process's `on_exit` no longer clobbers the new one.
- Empty-list placeholders ("No backups found", …) were selectable and sent as values → disabled options.
- Mods for vanilla were written to `plugins/` → `paths.content_folder` is now the single mapping
  (neoforge added); vanilla → 400.
- Hidden `#legacy-auth-hidden` form with `admin`/`admin` and `__LEGACY_CONSOLE_OUTPUT` removed;
  its pinning test replaced by `test_dashboard_does_not_embed_credentials`.
- Tests wrote to the real `EZ_Server_Data/` (incl. overwriting `playit_config.json` with
  `demo-secret` via `test_playit_start_route_starts_the_tunnel`) → autouse `isolated_data_dir` fixture.

## Documentation vs implementation conflicts (open)
1. **`/api/status` mutates runtime** (sets loader/version/lan/public/playit) on a GET; side-effectful read.
2. **Browser never uses `/ws/console`** — browsers cannot set `Authorization` on a WebSocket; the
   route is only usable by non-browser clients (and the test). The GUI polls REST instead.
## Security
Fixed 2026-10-22:
- `GET /api/playit/config` returned the PlayIt secret key → now `{address, secret_present}` only.
- Backup restore accepted any filesystem path and extracted without validation (zip-slip) → only
  `world-backup-*.zip` files in the current server dir (full path reduced to its basename), every
  member must stay under `world/` (`_is_safe_world_member`), and 409 while the server runs.
- Credentials compared with `!=` → `secrets.compare_digest` (`auth.py::_credentials_match`).
- `run.bat` hard-coded `admin`/`admin` → uses env vars or prompts (password hidden).
- `crash_reporter.DEV_EMAIL` third-party address → module deleted.

Open / by design:
- UI routes (`/`, `/login`, `/dashboard/*`, `/dashboard.{css,js}`, `/health`) are unauthenticated:
  static shells only; all data goes through authenticated `/api/*` (decisions.md).
- Credentials live in browser `sessionStorage` and travel as Basic auth (plain HTTP on the LAN,
  HTTPS through the Cloudflare link); **must fix** before relying on remote access (top of this file).
- `playit_config.json` stores the secret in plaintext on the host; `notify_config.json` stores the
  SMTP app password in plaintext (never returned by the API). Use an app password, not the account
  password, so it can be revoked on its own.

## Unverified / needs inspection
- License terms beyond "All rights reserved".
- **Termux / Android (added 2026-10-22, awaiting the owner's device test):** `termux-setup.sh`
  (pkg packages, pydantic-core build with `ANDROID_API_LEVEL`), `run.sh`, Java selection via
  `pkg install openjdk-21`, the PlayIt Linux agent under `termux-chroot` (DNS workaround;
  `EZ_PLAYIT_CHROOT=0` disables it), and Forge/NeoForge `unix_args.txt` launch. Only the Python
  logic is covered by tests; scripts were syntax-checked with `bash -n`.
- `cloudflared` on Termux (`pkg install cloudflared`) and its auto-restart after an unexpected exit
  on a real device.
- Termux 24/7 mode: runit service + `svlogger` log, `ezmc` commands, Termux:Boot script sourcing
  `start-services.sh` after a reboot.
- `run.bat` prompt flow not executed in this session (batch + PowerShell secure-string read).
