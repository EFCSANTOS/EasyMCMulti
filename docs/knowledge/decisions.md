# Decisions (verified) and Open Questions

## Verified
1. Two layers: `webapp/` HTTP, `core/` runtime (imports in `app.py`).
2. Factory `create_app()`; module-level `app = create_app()` for uvicorn.
3. Env-var Basic auth with fail-fast startup.
4. `RuntimeState` + RLock + `snapshot()` for thread-safe reads.
5. WS fan-out via per-client `asyncio.Queue` + `call_soon_threadsafe` (publisher is a thread).
6. Bounded buffers: history 2000, queue 250, replay 500.
7. File-based persistence; no DB.
8. kebab-case translation for `server.properties`.
9. Graceful stop (`"stop"`) before `kill()`.
10. Backwards-compatible route aliases (metrics, backups, mods).
11. Platform-aware PlayIt agent naming.
12. Dashboard rendered inline (Python f-string HTML) rather than Jinja templates; only `/login`
    reads a template file.
13. Backups are zip archives with pre-restore rename of `world/`.
14. Mods go to `mods/` for forge/fabric/neoforge, `plugins/` otherwise.

## Open questions
- Why does dashboard embed `value="admin"` credentials? (test asserts it.)
- Restart calls `stop_server` then immediately `start_server` — race with process exit.
- `psutil` absent on Linux → cpu=0, uptime=0; intended?
- `FALLBACK_VERSIONS` only on exception, not on empty; empty → 502.
- Roles of `crash_reporter`, `mailer`, `sysinfo` — unreferenced by `app.py`.
- No ADR history in repo; rationale inferred from code.