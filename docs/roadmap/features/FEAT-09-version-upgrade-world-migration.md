# FEAT-09 — Version upgrade with world migration

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | M | FEAT-01, FEAT-07 |

## Why
A new Minecraft version comes out and the group wants to update. Today, changing the version in
Server setup points the app at a **different server directory** (`servers/<loader>-<version>/`),
so the server starts with a brand-new world, no whitelist and no mods. To the owner it looks like
the world was deleted. Moving files by hand on a phone is not realistic.

## Current state
- data-model.md: "Switching loader/version in config switches to a different server dir (and
  world)." Worlds, `ez-worlds.json`, mods, whitelist/ops/bans, `server-icon.png` live per dir.
- The Worlds page can download one dir's world and upload it into another — a manual migration.

## Proposal
When the owner changes version (or loader) for an existing server, show a dialog:
**"Bring your worlds and settings to <new version>?"**
1. **Pre-checks**
   - Downgrade (new < old) → strong warning: worlds opened in a newer version usually can't be
     opened by an older one. Allowed only for a *copy*.
   - Modded: check every Modrinth-recorded mod for a version compatible with the new MC version
     (FEAT-07's bulk endpoint). List the ones without one; the owner chooses "migrate without
     them" or "wait".
   - Loader change (e.g. Paper → Fabric): mods/plugins don't carry over; world does.
2. **Migrate** (server stopped, lifecycle lock held): backup (FEAT-01) → install the new server →
   **copy** (not move) all worlds + `ez-worlds.json`, `whitelist.json`, `ops.json`,
   `banned-*.json`, `server-icon.png` → install compatible mod versions → save config.
3. The old directory stays untouched, so rolling back = switching the version back.
4. The first start of the migrated world shows a note that Minecraft upgrades chunks on first load
   (slow start is normal).

## Implementation notes
- `core/migrate.py` (new): `plan(old_sdir, new_loader, new_version)` → checks + list of actions;
  `apply(plan, log, progress)`. Runs in the background (FIX-02) with progress on the banner.
- Disk check: needs roughly world size × 1 free space; refuse with a clear message otherwise
  (FEAT-15 can then free space).
- Tests: plan detects downgrade and incompatible mods; apply copies worlds and lists and leaves the
  old dir unchanged.

## Acceptance criteria
- Changing 1.21.1 → 1.21.4 with three Fabric mods carries the world, whitelist and the compatible
  mods over in one confirmation, and the old version still works if selected again.

## Keep it small
No "clean up old version dirs" here — FEAT-15 handles disk space. No world optimisation
(`--forceUpgrade`) in the first version.
