# FEAT-14 — Server icon, MOTD preview and full properties editor

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | S | FIX-05 |

## Why
A custom icon and coloured MOTD in the multiplayer list make the server feel like *ours* — cheap,
visible fun for a friend group. And sooner or later the owner needs a property the Settings page
doesn't expose (`spawn-protection`, `hardcore`, `resource-pack`, `player-idle-timeout`…), which
today means editing files on a phone.

## Current state
- Settings page maps 16 config keys → `server.properties` (`_server_properties_from_config`);
  other keys in the file are preserved but not editable.
- No icon upload. MOTD is a plain text input.

## Proposal
1. **Server icon:** upload any PNG/JPEG → resized/cropped to 64×64 PNG → `server-icon.png` in the
   server dir (applies on next start). Preview + remove.
2. **MOTD editor:** live preview styled like the multiplayer list, a colour/format palette that
   inserts `§` codes, two-line support (`\n`), length indicator.
3. **"All properties" table** (Settings → Advanced, owner only): every key currently in
   `server.properties`, with a description and type for known keys (boolean switch, number,
   enum), free text for unknown ones. Keys managed by the regular form are shown read-only with
   "edit in Settings", so the two editors never fight.
   Extra properties are stored in config as `extra_properties: {key: value}` and written by
   `update_properties` alongside the managed ones.

## Implementation notes
- Image resize without new dependencies is awkward; options: accept only 64×64 PNGs and validate
  the header (zero deps), or resize in the browser with a `<canvas>` before upload (zero server
  deps — preferred). Server validates PNG signature, IHDR 64×64, size < 64 KB.
- Known-key metadata: a small dict in `webapp/` with type/description for ~40 common keys.
- Never allow editing `server-port` / `level-name` / `white-list` through the raw table (managed
  keys); reject keys with `=` or newlines.
- Tests: icon validation (wrong size → 400); extra property round trip; managed key via extra →
  422.

## Acceptance criteria
- The owner sets an icon and a two-colour MOTD from a phone and sees them in the multiplayer list
  after a restart.
