# FEAT-07 — Mod and plugin update checker

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | M | — (FEAT-01 for the safety backup, FEAT-04 to refresh the pack) |

## Why
Mods get bug fixes and security patches; outdated plugins on Paper are a common source of exploits.
Checking each mod's page by hand doesn't happen on a friends server.

## Current state
- `modrinth.install` installs the newest compatible version and records it in `.ez-modrinth.json`.
- No way to see whether a newer compatible version exists; reinstalling means remove + install.

## Proposal
- Mods page → **"Check for updates"** (and automatically once a day, piggybacking on the update
  check loop): lists mods with a newer version for the current loader + MC version, showing old
  → new version and a changelog excerpt (plain text, as the details dialog already does).
- **Update** per mod or **Update all**: requires the server to be stopped (jars are locked), takes
  a safety backup (FEAT-01), downloads the new jar, verifies its hash, replaces the old file, updates
  the record. Required dependencies of the new version are installed as today.
- Works for uploaded jars too when Modrinth recognises the file by hash.
- After updates, mark the client pack (FEAT-04) as changed and tell the owner friends need to
  re-import it — only when a mod with `client_side != unsupported` changed.

## Implementation notes
- Modrinth bulk endpoint `POST /version_files/update` with `{hashes, algorithm: "sha1", loaders,
  game_versions}` → newest version per file; one request for the whole folder.
- `core/modrinth.py`: `check_updates(dest_dir, loader, mc_version)` and `update(dest_dir, sha1,
  log)`; keep the old jar as `<name>.jar.old` until the server starts successfully once, then
  delete (simple rollback: rename back).
- Tests with `fake_modrinth`: update detected, file replaced, record updated, dependency added,
  running server → 409.

## Acceptance criteria
- One click shows which of 30 installed mods have updates, using a single Modrinth request.
- A failed download leaves the old jar in place.

## Keep it small
No automatic unattended mod updates — mod updates can break worlds; the owner clicks.
