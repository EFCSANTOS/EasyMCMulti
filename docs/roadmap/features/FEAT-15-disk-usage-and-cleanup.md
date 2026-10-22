# FEAT-15 — Disk usage view and cleanup

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | S | — |

## Why
Phones have limited storage, and this app quietly accumulates gigabytes: one full server dir per
loader+version ever tried, every backup, `*-pre-restore-*` world folders, multiple Java runtimes,
Forge `installer.jar`, gzipped logs. When storage fills up, Minecraft corrupts chunks on save —
the worst possible failure for a friends world.

## Current state
- Nothing reports disk usage. Worlds page shows per-world size; backups show names only.
- Nothing is ever deleted automatically except temp upload/download files.

## Proposal
- **Storage card** (Server setup or a small "Storage" page): free space on the data drive and a
  breakdown — per server dir (marked *active* / *unused since <date>*), backups, pre-restore
  folders, Java runtimes (marked unused if no server dir needs that major), logs, downloaded
  binaries.
- **Cleanup actions**, each with a confirm dialog showing what will be deleted and how much space
  it frees: delete an unused server dir (offer "backup its worlds first"), delete pre-restore
  folders older than N days, delete unused Java runtimes, delete `installer.jar` / `*.log.gz`
  older than 30 days.
- **Low-space alert** (email/Discord) below 2 GB or 10% free; refuse backups/migrations that would
  fill the disk (FEAT-01, FEAT-09).

## Implementation notes
- `core/storage.py` (new): `usage()` walking `DATA_DIR` with `os.scandir` (cache 5 minutes — a
  big world has many files); `shutil.disk_usage(DATA_DIR)` for free space.
- Deletion goes through names, never client paths (same rule as backups), and refuses the active
  server dir and anything while the server runs.
- Tests: usage sums on a synthetic tree; active dir deletion refused; unused Java detection.

## Acceptance criteria
- The owner can see what uses space and free it without a file manager, and can't delete the
  active world by mistake.
