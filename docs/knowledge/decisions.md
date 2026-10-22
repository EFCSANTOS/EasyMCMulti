# Decisions (verified) and Open Questions

## Verified
1. Two layers: `webapp/` HTTP, `core/` runtime. `webapp/` is split by area (QUAL-01, 2026-10-22):
   routers keep the closure style (`register(app, services)`) rather than classes, so the move was
   mechanical and `create_app()` stays re-creatable for tests.
2. Factory `create_app()`; module-level `app = create_app()` for uvicorn.
3. Env-var credentials with fail-fast startup; one owner account (friend accounts: FEAT-10).
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
15. Login sessions in memory (FIX-01, 2026-10-22): HttpOnly cookie, no session file, so a dashboard
    restart signs everyone out — acceptable for a friends server, and nothing to leak from disk.
    Basic auth kept for scripts behind the same lockout. Lockout is time-based (15 min), never
    permanent, so nobody can lock the owner out for good.
16. All runtime data under one portable `EZ_Server_Data/` dir (PyInstaller-aware `paths.base_dir`);
    `.gitignore` also ignores `build/`, `dist/`, `*.spec`.
17. Portable JRE per Java major downloaded on demand (no system Java required).
18. English-only UI and code text; no localization layer (conventions.md).
19. GUI console uses REST polling, not the WS (historically: browsers can't send Basic auth on a
    WebSocket). The WS accepts the session cookie since FIX-01; FEAT-11 moves the GUI to it.
20. Android support via Termux (2026-10-22): installed Java instead of downloads, `termux-chroot`
    for the PlayIt agent, psutil skipped (metrics fall back to `/proc/meminfo`).
21. Remote dashboard access through a **Cloudflare quick tunnel** (2026-10-22). First built on a
    second PlayIt TCP tunnel (owner's initial choice), but PlayIt's API rejects untyped tunnels
    and the website offered no custom TCP tunnel, so the owner switched to Cloudflare: HTTPS, no
    account, automatic. Trade-offs: random link that changes on restart; quick tunnels have no
    uptime guarantee. Shipped with the existing Basic auth by owner decision; sessions +
    lockout followed in FIX-01.
27. Server lifecycle runs in the background (FIX-02 / FIX-04, 2026-10-22): requests return 202 and
    the dashboard follows `/api/status`, because the Cloudflare link drops requests after ~100 s and
    a "failed" install the owner retries would race the first. One non-reentrant lifecycle lock,
    409 "Busy" for clicks, waiting only for background callers — no job queue or history.
26. Worlds via `level-name` (2026-10-22): switching is a config change, not a copy, so worlds
    never move and switching is instant; display names are metadata so renames are safe while the
    server runs. Mod installs resolve required dependencies and hide client-only projects because
    those are the most common causes of a server that won't start after "installing a mod".
25. Self-update via git (2026-10-22): the install is already a clone, so updating = fast-forward the
    tracked branch. Fast-forward only and refuse on local edits/unpushed commits (never overwrite
    the owner's work); roll back if dependency install fails; restart by exec-in-place so service
    supervision survives. Auto-install is opt-in and waits for an empty server.
24. Alerts (2026-10-22): plain SMTP (Gmail app password) rather than a third-party notification
    service, since the owner asked for email; a healthchecks.io-style heartbeat covers outages the
    app cannot report itself. Alerts are throttled per subject (10 min) except new-link mails.
    The remote dashboard link is **on by default** at the owner's request.
23. 24/7 operation (2026-10-22): crash auto-restart on by default but rate-limited (3 per 10 min)
    so a broken server can't loop; deliberate stops never count as crashes; the dashboard stops
    Minecraft on shutdown so service restarts don't orphan Java. On Termux, supervision is
    `termux-services` (runit) + Termux:Boot rather than a custom watchdog.
22. PlayIt setup is asynchronous and shows the approval link in the dashboard instead of opening a
    browser on the host, so it works headless (phone) and from any device.

## Resolved (2026-10-22)
- Hidden `value="admin"` form: dead markup → removed. `run.bat` now prompts for credentials.
- `crash_reporter`, `mailer`, `sysinfo`, `templates/dashboard.html`: desktop-launcher leftovers → deleted.
- Player count: implemented from console join/leave lines (data-model.md).
- Security findings from the audit fixed (verification.md "Security").
- UI page routes stay unauthenticated by design: they are static shells (no data); every `/api/*`
  call needs the session cookie (or Basic auth).
- Restart race → restart now waits for exit (workflows.md §6).
- French text in `core/` → translated to English.

## Open questions
- `FALLBACK_VERSIONS` only on exception, not on empty; empty → 502.
- Is Linux hosting a goal? PlayIt supports it; Java download does not (architecture.md).
- No ADR history in repo; rationale inferred from code.