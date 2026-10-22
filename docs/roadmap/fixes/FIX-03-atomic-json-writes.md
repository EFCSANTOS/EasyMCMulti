# FIX-03 — Atomic writes for config and metadata files

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Fix (data safety) | P0 | S | — |

## Why
Phones die, PCs get unplugged, Android kills Termux. If that happens while a JSON file is being
written, the file is left truncated. For `launcher_config.json` the consequence is nasty:
`config_store.load()` returns `{}` on invalid JSON, so the app silently falls back to the defaults
(`vanilla 1.21.7`) — a **different server directory** — and the friends' world appears to be gone.
With `autostart_server` on, the app may even install and start a fresh vanilla server.

## Current state
Plain `open(path, "w")` + `json.dump` in:
- `core/config_store.py::save` (and it swallows `OSError`, so a full disk silently loses changes)
- `core/notifier.py::save_config`
- `core/playit.py::save_config`
- `core/worlds.py` (`ez-worlds.json` metadata)
- `core/modrinth.py` (`.ez-modrinth.json` install record)
- `core/server_process.py::update_properties` (`server.properties`)

## Proposal
1. Add `core/fsutil.py::write_json_atomic(path, data)` and `write_text_atomic(path, text)`:
   write to `path + ".tmp"`, `flush()`, `os.fsync()`, then `os.replace()` (atomic on Windows and
   POSIX). Keep the previous file as `path + ".bak"` before replacing.
2. `load()` helpers: if the main file is missing or invalid **and** a `.bak` exists, load the
   `.bak` and log `[config] launcher_config.json was damaged; restored the previous copy`.
3. `config_store.save` stops swallowing `OSError`; `PUT /api/config` returns 500 with "Could not
   save settings (disk full?)" so the owner sees it.

## Implementation notes
- Replace the six writers above with the helper; match `core/` style (no type hints, plain
  docstrings, English).
- Windows: `os.replace` can fail with `PermissionError` if an antivirus holds the target briefly —
  retry 3× with 100 ms sleeps.
- Tests: corrupt `launcher_config.json` with a `.bak` present → loads the backup; save leaves no
  `.tmp` behind; `PUT /api/config` with a patched failing write → error status.
- The `.bak` files sit next to paths `isolated_data_dir` already redirects, so the fixture needs
  no new constants.

## Acceptance criteria
- Killing the process mid-save never yields an empty or partial JSON file.
- A damaged config is recovered from `.bak` and the event is visible in the console.

## Keep it small
No versioned config history — one `.bak` generation is enough; real history is what backups are for.
