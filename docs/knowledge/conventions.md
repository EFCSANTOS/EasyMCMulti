# Conventions (observed)

## Style
- `webapp/`: `from __future__ import annotations`, PEP 585/604 types (`dict[str, Any]`, `str | None`),
  English.
- **All text is English** — code comments, docstrings, log lines, error messages and UI strings.
  `core/` was translated from French on 2026-10-22; do not reintroduce other languages. There is no
  i18n/localization layer: UI strings are inline in `webapp/app.py` (page HTML) and
  `webapp/static/js/dashboard.js`.
- `core/`: no type hints (except a few in `playit.py`), no `__future__` import, plain docstrings.
- `core/` reports progress via injected callbacks `log(str)` / `progress(done, total)`; web app wires
  them to `append_console_line` / `update_install_state`.
- Constants UPPER_SNAKE; private helpers `_`-prefixed.
- Routes are sync `def`; only WS is `async def`.
- Multi-decorator aliasing for backwards compatibility (metrics, backups, mods).

## Error handling
- Auth → `HTTPException(401)` + `WWW-Authenticate: Basic`; WS → `close(1008)`.
- Missing env vars → `RuntimeError` at startup.
- Not-running server → 409; bad input → 400; upstream version failure → 502; create failure → 500.
- `ServerProcess.send` swallows `OSError`/`ValueError`.
- `_system_metrics_snapshot` broad `except Exception` → zeros.
- `WebSocketManager.publish` catches `RuntimeError`/`QueueFull`.

## Concurrency
- `RuntimeState.lock` RLock is taken only by `snapshot()`; `app.py` mutates fields without it.
- Sync routes run in FastAPI's threadpool; long work (install, PlayIt claim/tunnel) blocks that
  worker for the request's duration. No guard against concurrent start/create.
- `publish` callable from reader thread via `call_soon_threadsafe`.
- Server output on daemon thread; no asyncio in `core/`.

## Naming
- Config snake_case ↔ properties kebab-case.
- Routes `/api/<domain>[/<action>]`; UI `/dashboard/{section}`.
- Tests `test_<feature>_<expectation>`.

## Windows specifics
- `CREATE_NO_WINDOW` to hide console.
- ctypes `GlobalMemoryStatusEx` fallback when `psutil` missing.

## Security posture
- Basic auth, env-var credentials (constant-time compare), LAN-only.
- Never return secrets over HTTP; accept file names, not paths, from clients and resolve them inside
  the server dir; validate archive members before extracting.
- No CSRF, rate limiting, TLS in inspected code.
- `core/` error style: raise `RuntimeError` with an English message; `load_*` helpers return `{}` on
  `OSError`/`ValueError`.

## Frontend
- Dashboard pages are composed from `_SECTION_*` HTML constants via `_DASHBOARD_PAGES` in
  `webapp/app.py`; edit a section once and every page that shows it updates.
- `dashboard.js` functions must tolerate missing elements (each page renders only some sections):
  guard with `if (!element) return;`. Settings are read via the `CONFIG_FIELDS` table
  (element id → config key), whose keys must match `_default_config`.
- User feedback goes through `setStatus` → `#action-status` (present on every page); the overview's
  server summary uses `setServerSummary` → `#status-panel`.
- Call canonical `/api/server/...` routes, not the legacy aliases;
  `test_dashboard_js_only_calls_existing_api_routes` fails on unknown URLs.