# FEAT-08 — Crash diagnosis and log viewer

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | M | — |

## Why
When a server won't start, the owner sees "crashed" and a wall of Java stack trace. Most failures
on a friends server are one of a handful of known causes with a one-line fix. Telling the owner
"Mod *Sodium Extra* needs *Sodium*, which isn't installed — install it?" turns a dead evening into
a 10-second fix.

## Current state
- The console shows the live output (2000-line history in memory, lost on dashboard restart).
- Crash detection and email alerts exist (workflows.md §5a, §17); the email says *that* it crashed,
  not *why*.
- `logs/latest.log`, `logs/*.log.gz` and `crash-reports/*.txt` in the server dir are not exposed.

## Proposal
1. **Diagnosis** — on crash or failed start, scan the last ~300 console lines and the newest crash
   report for known patterns and show a card with cause + suggested action:
   | Pattern | Diagnosis | Action |
   |---|---|---|
   | `UnsupportedClassVersionError` / `class file version N` | Wrong Java for this MC/mod | Show required Java; re-run Java selection |
   | `java.lang.OutOfMemoryError` | RAM too low | Link to RAM setting (FEAT-13 suggestion) |
   | `FAILED TO BIND TO PORT` / `Address already in use` | Port busy (often an orphaned server) | Show the PID if found; offer kill (owner confirms) |
   | Fabric `Incompatible mods found` / `requires … which is missing` | Missing/incompatible dependency | Name the mod; offer Modrinth install |
   | Forge/NeoForge `Missing or unsupported mandatory dependencies` | Same | Same |
   | `Mod X is client-only` / `Attempted to load class … for invalid dist DEDICATED_SERVER` | Client-only mod on server | Offer to remove the jar |
   | `session.lock` / `already locked` | Another server uses the world | See FIX-04 |
   | `You need to agree to the EULA` | EULA not accepted | Re-accept |
2. **Log viewer** — Console page tab "Logs": list `logs/` and `crash-reports/` files with size
   and date; view (plain text, last 2000 lines, `.gz` decompressed) and download.
3. **Crash email/Discord** includes the one-line diagnosis.

## Implementation notes
- `core/diagnose.py` (new): `diagnose(lines, crash_report_text) -> list[{code, message, action}]`
  — pure function, easy to unit-test with saved real log snippets in `tests/fixtures/logs/`.
- Routes: `GET /api/server/diagnosis`, `GET /api/server/logs`, `GET /api/server/logs/{name}` (name
  only, resolved inside `logs/` or `crash-reports/` — same rule as backups).
- Tests: each pattern from a real snippet; path traversal on log names → 400.

## Acceptance criteria
- Starting a Fabric server with a mod missing its dependency shows the mod and dependency names
  and an Install button, without opening the console.

## Keep it small
Rule table only — no log upload to paste services (mclo.gs etc.) unless the owner clicks a
"share log" button in a later iteration.
