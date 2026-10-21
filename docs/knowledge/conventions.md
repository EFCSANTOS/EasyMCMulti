# Conventions (observed)

## Style
- `from __future__ import annotations` at top of every inspected module.
- Type hints on public functions; `dict[str, Any]` / `Deque[str]` style (PEP 585/604).
- French docstrings in `core/server_process.py`; English docstrings/identifiers elsewhere.
- Module-level constants UPPER_SNAKE (`KNOWN_LOADERS`, `FALLBACK_VERSIONS`, `CREATE_NO_WINDOW`,
  `_APP_START_TIME`).
- Private helpers prefixed `_` (`_default_config`, `_server_dir_for_config`, `_playit_on_exit`).

## Error handling
- Auth failures → `HTTPException(401)` with `WWW-Authenticate: Basic` (`webapp/auth.py`).
- Missing env vars → `RuntimeError` at startup (`validate_auth_settings`).
- `ServerProcess.send` swallows `OSError`/`ValueError` (best-effort).
- `_system_metrics_snapshot` wraps `ctypes` in broad `except Exception` → zeros.
- `WebSocketManager.publish` catches `RuntimeError` and `QueueFull`.

## Concurrency
- `RuntimeState.lock = threading.RLock`; mutate via `with lock`, read via `snapshot()`.
- `WebSocketManager.publish` is thread-safe (called from server reader thread) using
  `loop.call_soon_threadsafe`.
- Server output consumed on daemon thread; no `asyncio` mixing in `core/`.

## Naming
- Config keys: snake_case (JSON) ↔ kebab-case (`server.properties`).
- Routes: `/api/<domain>[/<action>]`; dashboard pages `/dashboard/{section}`;
  sections: overview, setup, advanced, mods, remote, backups, console.
- Test names describe behaviour: `test_<feature>_<expectation>`.

## Windows specifics
- `CREATE_NO_WINDOW = 0x08000000` on `win32` to hide console.
- `_system_metrics_snapshot` uses `ctypes.windll.kernel32.GlobalMemoryStatusEx` when `psutil` absent.

## Security posture
- Basic auth only; env-var credentials; LAN-only intent (README).
- No CSRF, no rate limiting, no TLS observed in inspected files.