# FEAT-05 — Discord webhook notifications

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P1 | S | — |

## Why
Friend groups usually already live in a Discord server. Email alerts reach only the owner; a
Discord message reaches everyone: "Server is up — Fabric 1.21.1 at example.joinmc.link",
"Alex joined", "Server restarting in 5 minutes". It's also simpler to set up than an SMTP app
password: paste one webhook URL.

## Current state
- `core/notifier.py` sends email (SMTP) and pings a heartbeat URL; `notify(category, subject,
  body)` in `create_app` with categories `server`, `link`, `tunnels`, `updates`, 10-minute throttle.
- Player join/leave is already parsed (`RuntimeState.track_player_event`).

## Proposal
- Server setup → Alerts card gains **Discord webhook URL** + per-event switches:
  server online/offline, crash, player joined/left, new dashboard link (owner-only channel
  advised), update available, backup failed.
- Two webhook slots: **Friends channel** (online/offline, joins, scheduled restart warnings,
  status page link) and **Owner channel** (crashes, new dashboard link, security alerts from FIX-01).
  The dashboard link must never go to the friends channel by default.
- Messages: plain text or a simple embed (title, description, color by event). No bot, no
  gateway connection — webhooks are one HTTPS POST.
- Respect Discord rate limits (honour `429` + `retry_after`); joins/leaves batched within 30 s
  ("Alex, Sam joined").

## Implementation notes
- `core/notifier.py`: `send_discord(url, content, embed=None)` with `requests`; validate the URL
  starts with `https://discord.com/api/webhooks/` (or `discordapp.com`).
- Generalise `notify()` to route each category to email and/or Discord; keep the throttle.
- Store webhook URLs like the SMTP password: never returned by the API (`*_set` booleans).
- Test button sends a test message to each configured webhook.
- Tests: stub `requests.post`; category routing; throttle; URL never in `GET /api/notifications`.

## Acceptance criteria
- With only a friends webhook configured, the group sees online/offline and join messages, and
  never the dashboard link.

## Keep it small
No Discord bot (slash commands, chat bridge both ways) — that's a separate process with a token
and gateway connection. A one-way chat relay (in-game chat → Discord) could be a later switch on
the same webhook.
