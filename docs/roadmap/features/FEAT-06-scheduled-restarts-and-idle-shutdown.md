# FEAT-06 — Scheduled restarts and idle shutdown

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | M | FEAT-01 (backup before restart), FIX-04 |

## Why
- **Daily restart:** modded servers in particular leak memory and slow down after a day or two of
  uptime; a 5 a.m. restart keeps them snappy.
- **Idle shutdown:** a friends server is empty most of the day. On a phone that means heat and
  battery wear; on a PC, power and noise. Stopping after N minutes with nobody online saves both.

## Current state
- Always-on: autostart, crash auto-restart (rate-limited), no schedules.
- Online players are tracked from join/leave lines (`runtime.online_players`).
- `auto_update_install` already waits for an empty server before updating — similar "only when
  empty" logic.

## Proposal
Server setup → **Schedule** card:
- **Daily restart** at HH:MM (host local time), off by default. Warns players in chat at 10, 5 and
  1 minute (`say Server restarting in 5 minutes`), takes a backup (FEAT-01), restarts. If nobody is
  online, skips the warnings.
- **Stop when empty** after N minutes (off by default; 15/30/60 presets). The timer starts when the
  last player leaves (or at start, if nobody joins). Sends a friends-channel message (FEAT-05):
  "Server stopped (empty for 30 min) — ask <owner> or start it from the dashboard".
- **Quiet hours** (optional): don't autostart or crash-restart between HH:MM and HH:MM.
- The "stopped because idle" state is shown on the overview so it isn't mistaken for a crash.

## Implementation notes
- One `scheduler_loop` thread (1-minute tick) owning daily-restart, idle-stop and backups
  (merge with FEAT-01's loop rather than adding another thread).
- Idle timer reads `runtime.online_players` + `last_player_left_at` (new field set in
  `track_player_event`).
- Idle stop and scheduled restart go through the same functions as the Stop/Restart routes with
  `stop_requested=True`, so they never count as crashes.
- Tests with a patched clock: restart fires once a day; warnings sent in order; idle stop after N
  minutes only when empty; a join cancels the idle timer.

## Acceptance criteria
- A scheduled restart with players online warns them three times and comes back up with a fresh
  backup.
- An empty server stops on time; the overview says why.

## Keep it small
No cron-expression editor — a single daily time and an idle timeout cover the use case. No
wake-on-join (see not-doing.md); FEAT-10 lets a friend press Start instead.
