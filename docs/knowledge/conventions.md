# Conventions (observed)

## Style
- `webapp/`: `from __future__ import annotations`, PEP 585/604 types (`dict[str, Any]`, `str | None`),
  English.
- `core/`: no type hints (except a few in `playit.py`), no `__future__` import; docstrings, comments,
  log and error messages mostly in French without accents (English in `playit._agent_binary_name`,
  `ensure_agent`, `modrinth.resolve_project_slug`). Match the surrounding module.
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
- Basic auth, env-var credentials, LAN-only.
- No CSRF, rate limiting, TLS in inspected code.
- Dashboard embeds `admin`/`admin` in a hidden, unused form (see verification.md).
- `core/` error style: raise `RuntimeError` with a French message; `load_*` helpers return `{}` on
  `OSError`/`ValueError`.