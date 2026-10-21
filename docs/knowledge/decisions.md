# Decisions (verified) and Open Questions

## Verified architectural decisions
1. **Two-layer split** `webapp/` (HTTP) vs `core/` (runtime), so launcher and web share one code path.
   Evidence: imports in `webapp/app.py` (`from core import config_store, downloaders, java_manager,
   modrinth, netinfo, paths, playit`; `from core.server_process import ServerProcess, ...`).
2. **Factory pattern** `create_app()` rather than module-level app construction, enabling isolated
   `TestClient` instances and monkeypatching (`tests/test_webapp_smoke.py`).
3. **Env-var Basic auth** with fail-fast at startup (`validate_auth_settings` raises `RuntimeError`).
4. **Thread-safe runtime state** via `RLock` + `snapshot()` rather than locks scattered in routes.
5. **Thread-safe WS fan-out** using per-client `asyncio.Queue` + `call_soon_threadsafe`, because
   `publish()` is called from the server reader thread.
6. **Bounded memory**: history 2000, per-client queue 250, reconnect replay 500.
7. **File-based persistence** (`server.properties`, `eula.txt`, JSON config) — no DB.
8. **kebab-case translation** in `update_properties` (`_`→`-`) to match Mojang property names.
9. **Platform-aware PlayIt agent** naming (`_agent_binary_name`), tested for linux arches.
10. **Graceful stop** = send `"stop"` (world save) before `kill()`.

## Open questions (need code/history)
- Why Basic auth instead of session/token? (No session store observed.)
- Why does the dashboard embed `value="admin"` credentials (test asserts it)? Intentional dev aid
  or leak?
- `psutil` optional with Windows `ctypes` fallback — is Linux without `psutil` supported (cpu=0)?
- `FALLBACK_VERSIONS` vs remote version list: when is each used?
- `netinfo`, `crash_reporter`, `mailer`, `sysinfo` roles — not inspected.
- No `docs/` or ADR history in repo listing; decisions above are inferred from code, not recorded rationale.