# FIX-02 — Run installs in the background (Cloudflare 100 s limit)

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Fix (reliability) | P0 | M | — |

## Why
Installing a server (Java download + server jar, or the Forge/NeoForge installer) takes minutes.
Today that work runs **inside the HTTP request**. Through the Cloudflare link, the edge gives up on
a request after about 100 seconds and the browser shows an error (HTTP 524) even though the install
continues — the owner clicks again and starts a second install into the same folder. On a phone
over mobile data this is the normal case, not an edge case.

## Current state
- `POST /api/server/create` and `POST /api/server/start` call `ensure_server_installed`
  synchronously (workflows.md §3: "Install runs synchronously inside the HTTP request").
- Install progress already exists: `update_install_state` feeds `install_status`,
  `install_progress`, `install_message` into `/api/status`, and the overview shows a progress banner.
- `POST /api/server/restart` also blocks up to 60 s + 10 s while the world saves.

## Proposal
- `create` and `start` return **202 Accepted** immediately with `{operation: "installing" |
  "starting"}` and do the work on a daemon thread (the same pattern `configure` uses for PlayIt).
- The dashboard already polls `/api/status` every 5 s; while `operation != "idle"` poll every 1 s
  and show the existing banner on every page, not just the overview.
- Failures land in `install_status="failed"` + `install_message`, a console line, and a toast the
  next time the status poll sees the failure.
- Restart: same treatment — return at once, finish on a thread.

## Implementation notes
- `webapp/app.py`: wrap the bodies of `create_server`, `launch_server` (when called from the
  route) and `restart_server` in a `run_in_background(name, fn)` helper that records the error in
  `runtime`. Reuse it for world activation (`POST /api/worlds/{folder}/activate`), which also stops
  and starts the server.
- Combine with FIX-04: the background job takes the lifecycle lock, so a second click returns 409
  "Already installing" instead of a second install.
- `dashboard.js::runAction`: treat 202 as "started" and let the status poll report completion.
- Tests: create returns 202 and `operation="installing"`; a monkeypatched slow install completes
  on the thread; a failing install sets `install_status="failed"`.

## Acceptance criteria
- Every lifecycle request returns in under 2 seconds regardless of download speed.
- Creating a Forge server through the Cloudflare link shows progress to completion with no error.
- Double-clicking Create starts exactly one install.

## Keep it small
No job queue or job history — one lifecycle operation at a time, tracked by the fields that
already exist in `RuntimeState`.
