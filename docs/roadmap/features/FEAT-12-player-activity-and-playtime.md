# FEAT-12 — Player activity log and playtime

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | S | FIX-03 |

## Why
Friends love a leaderboard ("Sam has 140 hours?!"), and the owner gets useful answers: who was
online when the base got griefed, whether anyone still plays (worth keeping the server running?),
which evenings are busy (when to schedule restarts, FEAT-06).

## Current state
- `RuntimeState.track_player_event(line)` keeps the current online set (cleared on start/exit).
  Nothing is persisted; history disappears with the console.

## Proposal
- Append every join/leave to `EZ_Server_Data/activity.jsonl`: `{t, event: join|leave|server_stop,
  name, server_dir}`. On server stop/crash, close open sessions with a `server_stop` marker so
  playtime isn't counted while the server was down.
- **Players page** (or a card): per player — last seen, total playtime, sessions count, first seen;
  "Online now" on top. Small bar chart "players online by hour of day, last 30 days".
- Feeds FEAT-02's "Recently seen" list (including rejected whitelist attempts) and FEAT-03's
  optional status page leaderboard.
- Retention: compact entries older than a year into per-player totals.

## Implementation notes
- `core/activity.py` (new): `record(event, name)`, `summary(since=None)` — read the JSONL
  sequentially; at friend scale (a few thousand lines a year) no index is needed.
- Hook into the existing `on_output` → `track_player_event` path and the `on_exit` handler.
- Chart: follow the dashboard's chart tokens (`--series-1`).
- Tests: synthetic join/leave/stop stream → correct playtime; crash mid-session closes the session.

## Acceptance criteria
- After a week of play, the page shows each friend's playtime within a minute of the true value.

## Keep it small
No per-player statistics from the world's `stats/*.json` (blocks mined, deaths) in the first
version — easy to add later by reading those files, but not needed for the core value.
