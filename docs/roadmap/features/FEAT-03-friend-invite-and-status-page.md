# FEAT-03 — Friend invite card and public status page

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P1 | M | — (FEAT-04 adds the modpack link) |

## Why
"What's the IP again?", "Which version?", "Is it on?", "Which mods do I need?" — the owner answers
these in the group chat over and over. The dashboard knows all the answers but is behind a login
that friends shouldn't have.

## Current state
- Overview shows LAN and internet join addresses with copy buttons; Remote page shows the PlayIt
  address. All behind login.
- Unauthenticated routes are static shells only (decisions.md "Resolved").

## Proposal
1. **Invite card** (overview, owner only): a "Copy invite" button producing a ready-to-paste
   message:
   ```
   Join our Minecraft server!
   Address: example.joinmc.link
   Version: Fabric 1.21.1 — mods required, get the pack here: <link>
   Status page: <link>
   ```
2. **Public status page** at `/s/<token>` (opt-in switch, off by default; token random and
   regenerable): server name/MOTD, online/offline, players online (names optional), join address
   with copy button, version and loader, and the modpack download (FEAT-04) when there is one.
   Read-only, no actions, nothing from the config beyond these fields. Auto-refreshes every 30 s.
3. The page is reachable through the Cloudflare link, so friends see it from anywhere — the link
   changes on restart, so it is also included in the "new link" alert/Discord message (FEAT-05).

## Implementation notes
- `webapp/app.py` (or `routes/public.py` after QUAL-01): `GET /s/{token}` renders an HTML page;
  `GET /s/{token}/status.json` for the refresh. Wrong token → 404 (not 401, nothing to probe).
- Config keys: `status_page_enabled` (False), `status_page_token`, `status_page_show_names` (True).
- No secrets, file paths or LAN IPs on the public page unless the owner chooses "show LAN address".
- Tests: disabled → 404; enabled → fields present and no config secrets in the HTML; regenerate
  invalidates the old token.

## Acceptance criteria
- A friend with only the status link can see whether the server is up and how to join.
- Turning the switch off makes the page 404 immediately.

## Keep it small
No player avatars from third-party skin services, no server list pinging other servers, no
public "start server" button (that's FEAT-10 with a login).
