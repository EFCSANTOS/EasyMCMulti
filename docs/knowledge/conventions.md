# Conventions (observed)

## Style
- `from __future__ import annotations` everywhere inspected.
- PEP 585/604 types (`dict[str, Any]`, `str | None`).
- French docstrings in `core/server_process.py`; English elsewhere.
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
- `RuntimeState.lock` RLock; mutate under lock, read via `snapshot()`.
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
- Dashboard embeds `admin`/`admin` in hidden form (see verification.md).