# Verification & Gaps

## Fetch status
| File | Status |
|---|---|
| webapp/app.py | **Fully read** (both blob and raw) |
| webapp/auth.py, state.py, websocket_manager.py | Fully read |
| core/server_process.py | Fully read |
| tests/test_webapp_smoke.py | Fully read |
| core/config_store.py, downloaders.py, java_manager.py, modrinth.py, netinfo.py, paths.py, playit.py | **Not read** — signatures inferred from `app.py` calls |

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

## Unverified / needs inspection
- `core/config_store.py`: `load()`/`save()` signature, file location, error behavior.
- `core/downloaders.py`: `prepare`, `launch_args`, `list_versions` signatures/behavior.
- `core/java_manager.py`: `ensure_java`, `required_java`.
- `core/modrinth.py`: `search`, `install` return types.
- `core/netinfo.py`: `lan_ip`, `public_ip` (network calls? caching?).
- `core/paths.py`: `server_dir` layout (base dir, sanitization).
- `core/playit.py`: `claim_interactive`, `ensure_agent`, `ensure_tunnel`, `PlayitAgent`,
  `_agent_binary_name`, `load_config`/`save_config` format.
- `crash_reporter.py`, `mailer.py`, `sysinfo.py`: purpose (unreferenced by `app.py`).
- `webapp/templates/login.html`, `webapp/static/css/dashboard.css`, `webapp/static/js/dashboard.js`
  (referenced, not read).
- `requirements.txt` versions; CI; license terms beyond "All rights reserved".

## Unresolved questions
- Is the hidden `admin`/`admin` form intentional (dev convenience) or leftover?
- Intended restart semantics (should it wait for exit)?
- Are `crash_reporter`/`mailer`/`sysinfo` dead code or used by launcher entry points not inspected?