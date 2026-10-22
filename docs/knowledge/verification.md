# Verification & Gaps

## Must fix
Nothing open. The login hardening that was here (sessions + lockout for the internet-facing
dashboard link, on by default) shipped as FIX-01 — see "Security" below. Remaining by-design
limit: Cloudflare sees traffic in clear at its edge (TLS terminates there).

## Fetch status
Audited 2026-10-22: all of `core/`, `webapp/` (incl. `static/js/*.js`, then one `dashboard.js`, `templates/*.html`),
`tests/`, `requirements.txt`, `run.bat`, `.env.example`, `.gitignore`, README. Smoke suite: 76 passed.
UI redesign (2026-10-22) verified with headless Edge screenshots at 1440 px and 390 px.
Same day: cleanup + English-only pass + GUI feature check (see "Fixed 2026-10-22").
Not audited line-by-line: `webapp/static/css/dashboard.css` (presentation only).
GUI verified by: smoke tests, a live uvicorn boot hitting every page and the GUI's GET APIs, and
`node --check` on `dashboard.js`. Not verified in a real browser session or with a real Minecraft
server / PlayIt agent.

## Verified route table
UI: `/`, `/login`, `/dashboard`, `/dashboard/{section}`, `/dashboard.css`, `/js/{name}` (only names in
`layout.SCRIPTS`).
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
PlayIt: `GET /api/playit/status`, `POST /api/playit/{configure,start,stop}`, `GET`/`PUT /api/playit/config`.
Remote dashboard: `GET/POST /api/remote-dashboard`.
Auth: `POST /api/login`, `POST /api/logout`, `GET /api/session`.
WS: `/ws/console` (session cookie or `Authorization: Basic`; close 1008 on failure).

## GUI feature status (dashboard ↔ API)
| Feature | Status |
|---|---|
| Login / sign out | Works — HttpOnly session cookie, lockout after 5 wrong passwords (FIX-01) |
| Loader + version pickers | Works — versions fetched from `/api/versions/{loader}`, refreshed on loader change |
| Save config (Setup and Advanced pages) | Works — saves only the fields present on the page |
| Create / start / stop / restart server | Works — background jobs (202), one at a time (409 "Busy"), progress banner on every page; live-checked in headless Edge with a slow fake install |
| Console | Works — polled every 2 s via `GET /api/console` |
| Console commands, admin buttons | Works (409 when server not running) |
| Backup / list / restore | Works |
| Mod updates (check, update, undo) | Works in tests (fake Modrinth: one request, hash check, dependency, rollback, cleanup) and against the live Modrinth API (old Lithium 0.13.0 → 0.15.4 offered, downloaded, hash-verified, `.old` kept, re-check empty); UI not yet clicked through with real mods |
| Crash diagnosis + log viewer | Works in tests (8 real-format log fixtures in `tests/fixtures/logs/`, alert text, fixes, traversal) and headless Edge (fake Fabric crash → card with "Install sodium" on Overview without opening the console; log list and viewer) |
| Schedule (daily restart with chat warnings, idle stop, quiet hours) | Works in tests with a patched clock and headless Edge (settings save and reload); **not yet run against a real Minecraft server** |
| Scheduled backups, retention, safety backups | Works in tests (patched clock, quiet save sequence, low-disk guard, delete safety) and headless Edge (manual backup listed with kind/size/total, schedule saved); **quiet save not yet run against a real Minecraft server** |
| Mod browser (browse/category/sort/details, install + dependencies, remove), upload jars | Works; live read-only check against Modrinth; vanilla → 400 with explanation |
| Discord alerts (owner + friends webhooks) | Works in tests (routing, batching, switches, rate limit, URL never returned) and headless Edge (invalid URL rejected, save/remove); **not yet posted to a real Discord webhook** |
| Mod pack export (.mrpack) | Works in tests (faked Modrinth) and against the live Modrinth API (Fabric API, Lithium, spark: every CDN download matched the index's SHA-512 and size); headless Edge export downloads the file. **Not yet imported into Prism Launcher / Modrinth App** |
| Invite friends + public status page | Works in tests and headless Edge (invite text, enable/regenerate/disable, signed-out public page at desktop and phone width); copy uses a textarea fallback when the Clipboard API is denied |
| Players (whitelist on/off, add/remove, operators with level, unban, recently seen) | Works in tests (commands while running, JSON + offline/Mojang UUIDs while stopped) and in headless Edge (stopped server, desktop + phone width); not yet tried against a real running server |
| Worlds (upload, switch, rename, download, delete, new) | Works in tests (incl. real zip handling); not yet tried with a large real world over the Cloudflare link (~100 MB upload limit) |
| PlayIt configure / start / stop / status | Works; configure runs in the background and the dashboard shows the approval link (open from any device); "Change account" resets |
| Remote dashboard (Cloudflare) | Works: live-checked on Windows 2026-10-22 (download, registration-gated link, request served through `trycloudflare.com`); login has sessions + lockout |
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
1. ~~`/api/status` mutates runtime~~ → fixed by FIX-06: a pure read that merges derived values.
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
- Login was Basic auth re-sent on every request, kept in `sessionStorage`, with no limit on wrong
  passwords (FIX-01) → `POST /api/login` sets an in-memory session cookie (`ezmc_session`,
  HttpOnly, SameSite=Strict, Secure via the tunnel, 7-day sliding expiry); 5 wrong passwords per IP
  in 15 min, or 30 overall, lock sign-in for 15 min (429 + `Retry-After`) and send a `security`
  alert. Basic auth still works for scripts and shares the counter. Cookie-authenticated writes must
  be JSON/multipart from the same origin, and cookie WebSockets same-origin (CSRF/CSWSH).
  `CF-Connecting-IP` is trusted only from loopback (cloudflared). Weak password (< 12
  chars) with the link on → startup warning + Remote page banner.

Open / by design:
- UI routes (`/`, `/login`, `/dashboard/*`, `/dashboard.css`, `/js/*`, `/health`) are unauthenticated:
  static shells only; all data goes through authenticated `/api/*` (decisions.md).
- Public status page `/s/<token>` (opt-in, off by default): anyone with the link sees the server
  name, state, player count (and names unless hidden) and join address. Without PlayIt the address
  is the owner's **public IP**; the switch hint says so. Tests assert no secrets, emails, the LAN IP
  (unless "show LAN" is on) or the token itself appear in the page or JSON.
- On the LAN the dashboard is plain HTTP, so the password crosses the network in clear once per
  login (HTTPS through the Cloudflare link). Sessions live in memory: a dashboard restart signs
  everyone out.
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
- Automatic Termux installs (2026-10-22): `hostenv.termux_install` for `openjdk-25` / `cloudflared`
  / `proot` is tested with a stubbed apt only; not yet run on a device. Same for
  `playit.kill_stale_agents` (`/proc` cmdline matching under termux-chroot/proot) and the
  process-group `killpg` stop.
- Termux 24/7 mode: runit service + `svlogger` log, `ezmc` commands, Termux:Boot script sourcing
  `start-services.sh` after a reboot.
- `run.bat` prompt flow not executed in this session (batch + PowerShell secure-string read).
