# Verification & Gaps

## Fetch status
Audited 2026-10-22: all of `core/`, `webapp/` (incl. `static/js/dashboard.js`, `templates/*.html`),
`tests/`, `requirements.txt`, `run.bat`, `.env.example`, `.gitignore`, README. Smoke suite: 22 passed.
Not audited line-by-line: `webapp/static/css/dashboard.css` (presentation only).

## Verified route table
UI: `/`, `/login`, `/dashboard`, `/dashboard/{section}`, `/dashboard.css`, `/dashboard.js`.
Health: `/health`.
Metrics aliases: `/api/system/metrics`, `/api/system`, `/api/stats`, `/api/metrics`, `/metrics`.
Status/console: `/api/status`, `/api/console`.
Config: `GET/PUT /api/config`.
Versions: `/api/versions/{loader}`.
Server: `POST /api/server/{create,start,stop,restart,command,backup}`.
Backups: `GET /api/server/backups`, `POST /api/server/backups/restore`, `GET /api/backups`,
`POST /api/backups/restore`.
Mods: `GET /api/server/mods`, `POST /api/server/mods/upload`, `GET /api/mods`.
Admin: `POST /api/admin/{action}`.
Modrinth: `GET /api/modrinth/search`, `POST /api/modrinth/install`.
PlayIt: `GET /api/playit/status`, `POST /api/playit/{configure,start,stop}`, `GET /api/playit/config`.
WS: `/ws/console` (auth via `Authorization: Basic`; close 1008 on failure).

## Documentation vs implementation conflicts
1. **Credentials in dashboard HTML.** `render_dashboard_page` emits
   `<form id="legacy-auth-hidden" style="display:none;"><input name="username" value="admin" />
   <input name="password" value="admin" /></form>`. `test_dashboard_uses_runtime_auth_defaults`
   asserts this. README says credentials come from env vars and app is LAN-only. This is a real
   inconsistency: real creds are not rendered (only literal `admin`), but shipping default
   credentials in markup is a security smell and contradicts the README's security note.
2. **Restart race.** `restart_server` calls `stop_server` (sets `"stopping"`, sends `"stop"`) then
   immediately `start_server`, which checks `runtime.process.is_running()`. Since `stop()` only
   writes to stdin, the process is likely still running → `start_server` returns
   `"Server already running."` So restart is effectively a stop. **Behavioral bug** (verified from code).
3. **README "create server"** — actual route is `POST /api/server/create` (README doesn't name it).
4. **README "config persistence for loader, version, RAM"** — full 20-key schema exists; README
   understates.
5. **`/api/status` mutates runtime** (sets loader/version/lan/public/playit) on a GET; side-effectful
   read. Not a doc conflict but worth noting.
6. **Admin `op`/`deop`/`kick`/`ban` without player** produce commands `"op"`, `"deop"`, `"kick"`,
   `"ban"` and then 400 via `endswith` checks — the `endswith("op")` check also matches `"deop"`,
   so deop-without-player is caught; but `"op"` also matches `"deop"` suffix logic — works by accident.
7. **README** lists `run_debug.bat` (absent from repo) and omits `webapp/static/`, `webapp/templates/`, `tests/`.

## Frontend ↔ API mismatches (verified defects)
`webapp/static/js/dashboard.js` vs `webapp/app.py`:
1. **Mod upload always fails.** `uploadMods` POSTs to `/api/mods/upload`; route is
   `/api/server/mods/upload` (`app.py::upload_mods`). No such alias exists.
2. **Three settings never reach `server.properties`.** `saveConfigFromForm` sends `whitelist`,
   `command_blocks`, `seed`; backend reads `white_list`, `enable_command_block`, `level_seed`
   (`_server_properties_from_config`). Wrong keys are persisted as extra config keys and ignored.
   `loadConfig` also reads `config.seed`.
3. **Save config only works on `/` (overview).** `saveConfigFromForm` dereferences the advanced
   inputs (`online-mode`, `motd-input`, …) unguarded; `/dashboard/setup` has `setup-form` but not
   those inputs → `TypeError` before the request is sent. `/dashboard/advanced` has the inputs but no
   save control.
4. **Browser never uses `/ws/console`.** No `WebSocket` in `dashboard.js`; console is fetched once at
   `boot()` via `refreshConsole()` (`GET /api/console`) and not polled. Browsers cannot set an
   `Authorization` header on a WebSocket, so the WS route as implemented is only usable by non-browser
   clients (and the test).
5. **Hidden `#legacy-auth-hidden` form is dead markup** — nothing in `dashboard.js` reads it. Real
   browser auth: `login.html` validates via `GET /api/status`, then stores username/password in
   `sessionStorage` (`ez_auth_username`/`ez_auth_password`); `dashboard.js::buildBasicAuth` sends them
   on every call. `webapp/templates/dashboard.html` duplicates the inline page and is not served.
6. **`/api/playit/status` reports `"running"`** when the saved config has secret + address, even if no
   agent process is running (`app.py::playit_status`).

## Security findings (not covered by README security note)
- `GET /api/playit/config` returns `playit_config.json` verbatim, **including the PlayIt secret key**.
- UI routes (`/`, `/login`, `/dashboard/*`, `/dashboard.{css,js}`, `/health`) are unauthenticated;
  only `/api/*` and `/ws/console` check credentials.
- `POST /api/server/backups/restore` accepts any existing filesystem path (`backup_path` or `name`) and
  calls `ZipFile.extractall(target_dir)` without member validation (zip-slip possible).
- Credentials compared with `!=` (`auth.py::verify_credentials`), not `secrets.compare_digest`.
- `run.bat` hard-codes `EZ_WEBAPP_USERNAME/PASSWORD=admin`.

## Unverified / needs inspection
- License terms beyond "All rights reserved".

## Unresolved questions
- Intended restart semantics (should it wait for exit)?
- Should `test_dashboard_uses_runtime_auth_defaults` keep pinning the dead hidden form?