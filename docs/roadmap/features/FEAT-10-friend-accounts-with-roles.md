# FEAT-10 — Friend accounts with limited permissions

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | M | FIX-01, FIX-05 |

## Why
The owner is asleep, at work or out of battery, the server stopped (idle shutdown, crash limit,
reboot), and the friends can't play. Sharing the owner password is the usual workaround — and it
hands over everything: settings, mods, world deletion, the PlayIt account. A friend account that
can **start the server and see who's on**, but not break anything, removes the bottleneck.

## Current state
- One account from `EZ_WEBAPP_USERNAME` / `EZ_WEBAPP_PASSWORD`; every authenticated request can do
  everything.

## Proposal
- Two roles only:
  | Action | Owner | Friend |
  |---|---|---|
  | View overview, players, status | ✓ | ✓ |
  | Start server | ✓ | ✓ |
  | Restart / stop | ✓ | optional switch (default off) |
  | Chat (`say`) from dashboard | ✓ | ✓ |
  | Create backup | ✓ | ✓ |
  | Console commands, admin buttons, whitelist/ops | ✓ | — |
  | Settings, mods, worlds, restore, remote, updates, alerts | ✓ | — |
- Owner manages friend accounts on a **Users** page: add (username + generated password to send
  them), reset password, remove. The env-var account is always the owner, so the owner can never
  be locked out.
- Friends see a reduced sidebar; hidden pages also return 403 from the API.
- Audit: console line `[dashboard] alex started the server` for every friend action.

## Implementation notes
- `EZ_Server_Data/users.json`: `{username: {role, salt, hash, created}}` with
  `hashlib.scrypt` (stdlib — no `bcrypt`/`argon2` builds on Termux). Atomic writes (FIX-03).
- `webapp/auth.py`: sessions (FIX-01) store `(username, role)`; dependencies
  `require_auth` (any role) and `require_owner`. Default every existing route to `require_owner`
  and open specific ones to friends, so new routes are owner-only unless decided otherwise.
- `dashboard.js`: `GET /api/me` → role; hide nav items and buttons accordingly.
- Tests: friend → 403 on every owner route (table-driven over the route list); friend can start;
  owner env account always works; deleted friend's session is invalidated.

## Acceptance criteria
- A friend can log in through the dashboard link on their phone and start the server, and gets 403
  on settings, console commands and world management.

## Keep it small
Two fixed roles, no per-permission editor, no self-registration, no password reset by email.
