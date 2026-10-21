# Verification & Gaps

## Fetch status
| File | Status |
|---|---|
| core/server_process.py | Fully read |
| webapp/state.py | Fully read |
| webapp/auth.py | Fully read |
| webapp/websocket_manager.py | Fully read |
| tests/test_webapp_smoke.py | Fully read |
| webapp/app.py | **Truncated** — cut inside `render_dashboard_page` HTML (~`setup`/`advanced`); route decorators and most handlers not captured |

## Claims requiring re-check against `webapp/app.py`
- Exact route paths/methods for `/api/status`, `/api/config` (GET/PUT), `/api/server/{start,stop,restart,command}`,
  `/api/console`, `/api/backups` (GET/POST/restore), `/api/mods` (GET/POST/upload/install),
  `/api/admin/*`, `/api/playit/{status,config,start,stop}`, `/api/system/metrics`,
  `/api/versions/{loader}`, `/ws/console`, `/dashboard/{section}`, `/health`.
- Whether `/` redirects to `/dashboard/overview` (`RedirectResponse` imported but target unseen).
- How `runtime.state` is set to `"running"`/`"stopped"`; `on_exit` wiring to `RuntimeState`.
- `netinfo` usage for `lan_address` / `public_address`.
- `playit._agent_binary_name` mapping table (only linux cases tested).
- `paths.server_dir` layout.
- `config_store.save` signature.
- `downloaders.prepare` / `launch_args` signatures (inferred from test monkeypatch only).
- `modrinth.install` signature (inferred from monkeypatch).
- `crash_reporter`, `mailer`, `sysinfo` — unreferenced in inspected code; purpose unknown.

## Documentation vs implementation conflicts
1. README says "Basic auth is enabled … credentials must be set through environment variables" —
   consistent with `auth.py`. But `test_dashboard_uses_runtime_auth_defaults` asserts the dashboard
   HTML contains `value="admin"` and a password input with `value="admin"`, i.e. credentials are
   rendered into the page. This conflicts with the security note. **Needs confirmation in `app.py`.**
2. README lists `webapp/state.py` but describes "config persistence for loader, version, RAM" —
   `RuntimeState` is in-memory only; persistence is `core/config_store`. Wording imprecise.
3. README "server creation, start, stop, and command routes" — creation route name not seen
   (`/api/server/create`?); only start/stop/command verified by tests.

## Unresolved
- Full `webapp/app.py` (largest, most central file).
- All `core/` modules except `server_process.py`.
- `requirements.txt` exact versions.
- CI configuration, license terms beyond "All rights reserved".
- Whether dashboard HTML embeds credentials intentionally (dev convenience) or is a bug.