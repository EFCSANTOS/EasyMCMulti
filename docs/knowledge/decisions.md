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
9. Graceful stop: `ServerProcess.stop()` sends `"stop"`. Only restart escalates to `kill()` after
   `RESTART_STOP_TIMEOUT_SEC`; plain stop has no timeout.
10. Backwards-compatible route aliases (metrics, backups, mods).
11. Platform-aware PlayIt agent naming.
12. Dashboard rendered inline (Python HTML string constants composed per page) rather than Jinja
    templates; only `/login` reads a template file (`templates/login.html`).
13. Backups are zip archives with pre-restore rename of `world/`.
14. Mods go to `mods/` for forge/fabric/neoforge, `plugins/` for paper; vanilla rejects mods
    (`paths.content_folder` is the single source).
15. Stateless client-side auth: creds kept in browser `sessionStorage`, re-sent as Basic on each call.
16. All runtime data under one portable `EZ_Server_Data/` dir (PyInstaller-aware `paths.base_dir`);
    `.gitignore` also ignores `build/`, `dist/`, `*.spec`.
17. Portable JRE per Java major downloaded on demand (no system Java required).
18. English-only UI and code text; no localization layer (conventions.md).
19. GUI console uses REST polling, not the WS, because browsers cannot send Basic auth on a WebSocket.

## Resolved (2026-10-22)
- Hidden `value="admin"` form: dead markup → removed. `run.bat` now prompts for credentials.
- `crash_reporter`, `mailer`, `sysinfo`, `templates/dashboard.html`: desktop-launcher leftovers → deleted.
- Player count: implemented from console join/leave lines (data-model.md).
- Security findings from the audit fixed (verification.md "Security").
- UI page routes stay unauthenticated by design: they are static shells (no data); login is
  client-side and every `/api/*` call carries Basic auth.
- Restart race → restart now waits for exit (workflows.md §6).
- French text in `core/` → translated to English.

## Open questions
- `psutil` absent on Linux → cpu=0, uptime=0; intended? (`psutil` is in `requirements.txt`.)
- `FALLBACK_VERSIONS` only on exception, not on empty; empty → 502.
- Is Linux hosting a goal? PlayIt supports it; Java download does not (architecture.md).
- No ADR history in repo; rationale inferred from code.