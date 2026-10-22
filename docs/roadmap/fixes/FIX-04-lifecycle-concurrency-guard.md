# FIX-04 — Guard against concurrent start/create/restart

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Fix (correctness) | P1 | S | — (pairs well with FIX-02) |

## Why
With friends able to reach the dashboard (and later FEAT-10 friend accounts), two people pressing
Start, or Start racing a crash auto-restart, an update restart or a world switch, can launch two
Java processes against the same world. The second one fails on the `session.lock` at best, and
`runtime.process` ends up pointing at the wrong process at worst.

## Current state
- conventions.md: "No guard against concurrent start/create."
- `RuntimeState.lock` (an `RLock`) is only taken by `snapshot()`; routes and timers mutate
  `runtime` fields without it.
- Starters today: `POST /api/server/start|create|restart`, world activate, `restart_after_crash`
  (timer thread), `autostart()`, resume-after-update, and (future) FEAT-06 schedules.

## Proposal
- One **lifecycle lock** (`threading.Lock`, non-reentrant) held for the whole of any operation that
  changes the server process: install, start, stop-and-wait, restart, world switch, restore.
- Routes use `lock.acquire(blocking=False)`; if taken → **409** `"Busy: <operation> in progress."`
  Background callers (crash timer, schedules) wait with a timeout instead, then re-check state.
- Mutations of `runtime.state` / `operation` / `process` go through small helpers that take
  `runtime.lock`, so `snapshot()` never sees a half-updated state.

## Implementation notes
- `webapp/state.py`: add `lifecycle: threading.Lock` to `RuntimeState`, plus a
  `set_state(state, operation=None)` helper.
- `webapp/app.py`: wrap `launch_server`, `create_server`, restart, world activate, restore.
  `restart_after_crash` already re-checks `stop_requested` and whether a server runs — keep that
  check but do it after acquiring the lock.
- Tests: two threads call start with a slow fake install → exactly one `RecordingProcess`; a
  start during restore → 409.

## Acceptance criteria
- No sequence of clicks, timers and API calls ever produces two Java processes.
- The UI shows a clear "Busy" message rather than an error trace.
