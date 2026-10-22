# QUAL-01 — Split `webapp/app.py` into routers and page modules

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Code quality | P2 | M | — (do it right before FEAT-02/03/10 add pages and routes) |

## Why
`webapp/app.py` is ~104 KB in a single file: every route, the background threads (crash restart,
heartbeat, update loop, tunnels), and all page HTML as string constants. Most roadmap items add
routes and pages; in one file they get harder to review, and merge conflicts get likely.

## Current state
- `create_app()` defines routes as closures that capture `runtime`, `console_manager`,
  `append_console_line`, `notify`, etc.
- Pages: `_SECTION_*` HTML constants composed via `_DASHBOARD_PAGES` (decisions.md #12).
- `dashboard.js` (~60 KB) is one file as well.

## Proposal
Mechanical split, no behaviour change:
```
webapp/
  app.py            create_app(): build services, include routers, lifespan
  services.py       AppServices: runtime, console, notify, lifecycle helpers (launch_server, …)
  routes/
    server.py       create/start/stop/restart/command/admin
    config.py       config, versions
    backups.py      backups
    worlds.py       worlds
    mods.py         mods + Modrinth
    remote.py       PlayIt + Cloudflare
    system.py       status, metrics, health, updates, notifications
    pages.py        dashboard shells, login, assets
  pages/            _SECTION_* constants per page (sections.py, layout.py, icons.py)
  background.py     crash restart, heartbeat, update loop (later: scheduler, FEAT-01/06)
```
- Routers get services through `request.app.state.services` (or a `Depends(get_services)`), not
  module globals — keeps `create_app()` re-creatable for tests.
- Split `dashboard.js` into a few plain `<script>` files (`api.js`, `console.js`, `mods.js`, …)
  loaded in order — still no build step. Update `ASSET_VERSION` to cover all of them and the
  JS↔API contract test to scan every file.

## Implementation notes
- Move code in small commits (one router per commit), running the smoke suite after each.
- Keep route paths, function names used by tests, and monkeypatch targets stable, or update the
  tests in the same commit.
- Update `docs/knowledge/architecture.md` module map at the end.

## Acceptance criteria
- Same route table (compare `app.routes` before/after), same test results, no file over ~25 KB.
