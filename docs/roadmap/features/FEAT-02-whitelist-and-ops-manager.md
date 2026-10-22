# FEAT-02 — Whitelist and operators manager

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P1 | M | FIX-05 |

## Why
The single most useful protection for a friends server reachable through PlayIt is the whitelist:
only the group can join. The dashboard already has a "Whitelist" switch, but no way to *add*
anyone — the owner has to know the `whitelist add <name>` command, and it only works while the
server is running. Likewise ops and bans are only typeable, never visible.

## Current state
- Settings page: `whitelist-mode` switch → `white_list` → `white-list=true` in `server.properties`.
- Console page: Players card with op/deop/kick/ban buttons that take a typed name
  (`POST /api/admin/{action}`), no lists.
- The server keeps `whitelist.json`, `ops.json`, `banned-players.json`, `banned-ips.json` in the
  server dir; nothing reads them.

## Proposal
A **Players** page (or a card on the console page) with three lists:
- **Whitelist** — names with Add / Remove. Shows a warning banner if the whitelist is off.
- **Operators** — names with level (1–4) and Add / Remove.
- **Banned** — names with reason and Unban.
Plus "Recently seen" names (from join lines, FEAT-12) with one-click "Add to whitelist", which
covers the common flow: friend tries to join → gets rejected → owner clicks their name.

Behaviour:
- **Server running:** send `whitelist add|remove`, `op|deop`, `pardon` commands (the server keeps
  the files in sync and resolves UUIDs itself), then re-read the JSON after the confirmation line.
- **Server stopped:** edit the JSON files directly. UUIDs come from the Mojang profile API
  (`api.mojang.com/users/profiles/minecraft/<name>`) when `online_mode` is on, and from the offline
  UUID (`UUID.nameUUIDFromBytes("OfflinePlayer:" + name)`, i.e. MD5-based v3) when it's off.
- Also capture names of rejected joins (`... lost connection: You are not white-listed`) so the
  "Recently seen" list includes people who tried to join.

## Implementation notes
- `core/players.py` (new): `read_list(sdir, kind)`, `write_list(...)` (atomic, FIX-03),
  `resolve_uuid(name, online_mode)`.
- Routes: `GET /api/players/lists`, `POST /api/players/{whitelist|ops|bans}` `{action, name,
  level?}`. All names through FIX-05 validation.
- `dashboard.js`: rows built with `itemRow` / `rowButton` (textContent only).
- Tests: stopped-server edits produce valid JSON with correct offline UUID; running-server path
  sends the right commands; invalid names → 400.

## Acceptance criteria
- The owner can whitelist a friend from a phone without typing a command, server on or off.
- The lists in the dashboard match the files on disk after any change.

## Keep it small
No IP-ban editor UI beyond showing and unbanning; no permission plugins (LuckPerms etc. are mods).
