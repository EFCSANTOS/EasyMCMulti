# FEAT-01 — Scheduled backups and retention

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P1 | M | FIX-03 |

## Why
The classic friends-server disaster: someone blows up the base with TNT, a mod corrupts a chunk,
or the phone's storage fails — and the last backup is from three weeks ago because backups only
happen when the owner remembers to click "Backup world". Backups should just happen. Without
retention they also fill a phone's storage within days.

## Current state
- `POST /api/server/backup` zips the active world on demand (`save-all flush` + 2 s sleep first).
- Backups live next to the world in the server dir: `world-backup-<folder>-<ts>.zip`. No
  schedule, no limit, no pre-update/pre-restore backups beyond the pre-restore folder rename.

## Proposal
- **Schedule** (Backups page card, saved in config):
  `backup_interval_hours` (0 = off, default 6), only while the server is running *or* it ran since
  the last backup (no point backing up an unchanged world).
- **Retention:** keep the last `backup_keep_recent` (default 8) plus one per day for
  `backup_keep_daily` days (default 7). Manual backups are labelled and never auto-deleted unless
  the owner opts in.
- **Automatic safety backups** before: version migration (FEAT-09), mod install/update (FEAT-07),
  restore, world delete, self-update. Labelled `auto-pre-<reason>`.
- **Quiet saving:** run `save-off` → `save-all flush` → wait for the "Saved the game" console line
  (timeout 30 s) → zip → `save-on`, instead of a fixed 2 s sleep, so the zip is consistent.
- **Low disk guard:** skip (and alert) if free space < 2 × the last backup size.
- Show size and age per backup; total backup space on the page.

## Implementation notes
- `core/backups.py` (new): `create(sdir, folder, label)`, `list(sdir)`, `prune(sdir, keep_recent,
  keep_daily)`, metadata in the archive name (`world-backup-<folder>-<ts>[-<label>].zip`) so
  `_is_backup_name` and existing backups keep working.
- Scheduler: a daemon thread started by `autostart()` that wakes every minute and checks
  `last_backup_at` — same style as `heartbeat_loop` / `update_check_loop`. Takes the lifecycle
  lock (FIX-04) only to read the process, not for the whole zip.
- Waiting for "Saved the game": subscribe a one-shot matcher on `append_console_line`.
- Alerts: `backup` category for failures and low disk.
- Tests: prune keeps the right set for a synthetic list of timestamps; schedule fires with a
  patched clock; manual backups survive pruning.

## Acceptance criteria
- With defaults, a world that is played daily always has a backup less than 6 hours old and never
  more than ~15 backups on disk.
- A failed backup produces a console line and an alert.

## Keep it small
No off-site/cloud upload in the first version. A later small step: "copy each backup to a folder"
(which can be a synced Google Drive/OneDrive/Syncthing folder) — no cloud SDKs.
