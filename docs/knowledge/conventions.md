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
- `RuntimeState.lock` RLock is taken by `snapshot()` and `set_state()`; change `state` and
  `operation` together through `set_state`.
- Sync routes run in FastAPI's threadpool. Anything slow must not run in the request: server
  lifecycle work goes through `start_lifecycle_job` (202 + background thread), PlayIt setup through
  its own background thread.
- Anything that changes the server process takes `runtime.lifecycle` — routes with
  `acquire(blocking=False)` → 409 `busy_error()`, background callers with a timeout
  (`launch_when_free`). It is not reentrant: inner helpers (`launch_server`, `stop_and_wait`,
  `request_stop`) assume the caller holds it.
- `publish` callable from reader thread via `call_soon_threadsafe`.
- Server output on daemon thread; no asyncio in `core/`.

## Naming
- Config snake_case ↔ properties kebab-case.
- Routes `/api/<domain>[/<action>]`; UI `/dashboard/{section}`.
- Tests `test_<feature>_<expectation>`.

## Files
- Write settings/metadata with `fsutil.write_json_atomic` / `write_text_atomic` and read them with
  `fsutil.read_json(path, default)` — never a bare `open(path, "w")` for anything the app needs to
  come back up (a truncated config silently switches to defaults and a different server dir).

## Windows specifics
- `CREATE_NO_WINDOW` to hide console.
- ctypes `GlobalMemoryStatusEx` fallback when `psutil` missing.

## Security posture
- Env-var credentials (constant-time compare); the browser uses the `ezmc_session` cookie, scripts
  may use Basic auth; both share the lockout. New routes take `Depends(require_auth)`.
- Never return secrets over HTTP; accept file names, not paths, from clients and resolve them inside
  the server dir; validate archive members before extracting.
- CSRF: cookie-authenticated writes must be JSON or multipart and same-origin, so `fetchJson`
  always sends a JSON body on non-GET. No TLS of its own (the Cloudflare link provides HTTPS).
- `core/` error style: raise `RuntimeError` with an English message; `load_*` helpers return `{}` on
  `OSError`/`ValueError`.

## Frontend
- Dashboard pages are composed from `_SECTION_*` HTML constants via `_DASHBOARD_PAGES` in
  `webapp/app.py`; edit a section once and every page that shows it updates.
- `dashboard.js` functions must tolerate missing elements (each page renders only some sections):
  guard with `if (!element) return;`. Settings are read via the `CONFIG_FIELDS` table
  (element id → config key), whose keys must match `_default_config`.
- User feedback goes through `setStatus(message, 'ok'|'error'|'pending')` → toast `#action-status`
  (present on every page). Wrap button-triggered requests in `runAction(button, pendingMessage, fn)`
  so the button disables and shows a spinner; `fn` returns the success message.
- Status values render via `setField(name, value)` into every `[data-field="name"]` element, so the
  same value can appear on several pages/cards without unique ids. `[data-copy-field]` buttons copy
  the sibling field's text (with a non-secure-context fallback for plain-HTTP LAN use).
- Lists (mods, search results, backups) are rows built with DOM APIs and `textContent` (`itemRow`,
  `rowButton`, `renderItems`) — never `innerHTML` with API data.
- Server-dependent controls: `renderServerControls` enables Start/Stop/Restart by state and disables
  `RUNNING_ONLY_SELECTOR` (console command + admin buttons) unless the server is running.
- Visual changes: verify with headless Edge screenshots (desktop and an iframe at phone width —
  headless Edge on Windows enforces a minimum window width).
- Call canonical `/api/server/...` routes, not the legacy aliases;
  `test_dashboard_js_only_calls_existing_api_routes` fails on unknown URLs.