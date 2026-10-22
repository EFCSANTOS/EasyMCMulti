# FEAT-04 — Client modpack export (`.mrpack`)

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P1 | M | — |

## Why
On Fabric/Forge/NeoForge, every friend needs the same mods and loader version as the server or
they can't join. Today the owner has to list the mods by hand and each friend installs them one
by one — the #1 reason modded friend servers die in week one. Modrinth's `.mrpack` format solves
this: one file, imported with one click in the Modrinth App, Prism Launcher, ATLauncher and others.

## Current state
- Mods installed from the dashboard are recorded in `<mods>/.ez-modrinth.json`
  (project id → slug, title, filename, version).
- Uploaded jars have no record.
- Client-only mods are hidden from the browser; server-only mods are installed like any other.

## Proposal
- **Mods page → "Export pack for friends"**: builds `<server name>-<mc version>.mrpack`:
  - `modrinth.index.json`: `formatVersion: 1`, `game: "minecraft"`, `name`, `versionId` (date),
    `dependencies` (`minecraft` + `fabric-loader` / `forge` / `neoforge` with the installed
    version), and `files[]` with `path` (`mods/<file>`), `hashes` (`sha1`, `sha512` computed
    locally), `downloads` (Modrinth CDN URL), `fileSize`, `env` (`client`/`server` from the
    project's `client_side`/`server_side`).
  - Mods with `client_side: unsupported` (server-only, e.g. many performance/admin mods) are left
    out.
  - Uploaded jars without a Modrinth record: look up by hash (`GET /version_file/{sha1}`); if still
    unknown, put them in `overrides/mods/` (with a warning that the pack is bigger and the owner
    must have the right to share them).
- Optional "Recommended client mods" list (e.g. a minimap) the owner picks from the browser with
  the client-only filter flipped, added with `env.server: unsupported`.
- The pack is served to friends from the public status page (FEAT-03) and the invite card.
- Paper servers: no pack needed (vanilla clients join) — the button is hidden and the invite says
  "no mods needed".

## Implementation notes
- `core/modpack.py` (new): `build_mrpack(sdir, loader, mc_version, loader_version, dest)`; bulk
  metadata via Modrinth `POST /version_files` (hash → version) to keep requests few.
- Need the installed loader version: Fabric from the server jar metadata/launcher properties,
  Forge/NeoForge from the installer's library folder names — record it at install time in the
  server dir (`ez-server.json`) so it doesn't have to be guessed later.
- Cache the built pack; rebuild when the mods folder's listing or mtimes change.
- Tests: index JSON validates against the format (required keys, hashes match file content);
  server-only mods excluded; unknown jar → `overrides/`.

## Acceptance criteria
- Importing the exported pack in Prism Launcher produces an instance that joins the server with no
  missing-mod errors.

## Keep it small
No hosting of packs on Modrinth, no CurseForge format, no auto-updating client launcher.
