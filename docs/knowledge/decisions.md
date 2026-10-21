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
9. Graceful stop only: `ServerProcess.stop()` sends `"stop"`; `kill()` exists but no code calls it
   (no timeout/escalation).
10. Backwards-compatible route aliases (metrics, backups, mods).
11. Platform-aware PlayIt agent naming.
12. Dashboard rendered inline (Python f-string HTML) rather than Jinja templates; only `/login`
    reads a template file (`templates/dashboard.html` exists but is not served).
13. Backups are zip archives with pre-restore rename of `world/`.
14. Mods go to `mods/` for forge/fabric/neoforge, `plugins/` otherwise.
15. Stateless client-side auth: creds kept in browser `sessionStorage`, re-sent as Basic on each call.
16. All runtime data under one portable `EZ_Server_Data/` dir (PyInstaller-aware `paths.base_dir`);
    `.gitignore` also ignores `build/`, `dist/`, `*.spec`.
17. Portable JRE per Java major downloaded on demand (no system Java required).

## Resolved (2026-10-22 audit)
- Hidden `value="admin"` form: unused by JS → dead markup; `run.bat` uses `admin`/`admin` too.
- `crash_reporter`, `mailer`, `sysinfo`: desktop-launcher leftovers, not imported by `webapp/`.

## Open questions
- Restart calls `stop_server` then immediately `start_server` — race with process exit.
- `psutil` absent on Linux → cpu=0, uptime=0; intended? (`psutil` is in `requirements.txt`.)
- `FALLBACK_VERSIONS` only on exception, not on empty; empty → 502.
- Is Linux hosting a goal? PlayIt supports it; Java download does not (architecture.md).
- No ADR history in repo; rationale inferred from code.