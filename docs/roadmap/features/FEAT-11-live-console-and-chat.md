# FEAT-11 — Live console, command history and chat box

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | S | FIX-01 (cookie auth on the WebSocket) |

## Why
The console refreshes every 2 seconds by re-downloading the whole history, which wastes data on a
phone and feels laggy while typing commands. Small comforts — arrow-up history, a chat-only view,
talking to friends in-game from the dashboard — make the console pleasant to use from bed.

## Current state
- `refreshConsole` polls `GET /api/console` (full `history`, up to 2000 lines) every 2 s
  (`CONSOLE_POLL_MS`); re-renders when the last line changes.
- `/ws/console` exists and streams, but browsers can't authenticate to it with Basic auth.

## Proposal
- **Live stream:** once FIX-01 lands, the dashboard opens `/ws/console` (cookie auth), renders the
  replay and appends new lines. Falls back to polling (with `?since=<n>` so only new lines come
  back) if the socket fails — Cloudflare quick tunnels do support WebSockets, but some networks
  don't.
- **Command history:** ↑/↓ cycle the last 50 commands (kept in `localStorage`, per browser).
- **Tab completion** of online player names (already known from `player_names`).
- **Filters:** All / Chat / Warnings & errors, plus a text search box. Lines tagged by simple
  regex on the log level (`/INFO]`, `/WARN]`, `/ERROR]`) and chat format (`<Name> message`).
- **Chat box** on the overview: sends `say <message>` (or `tellraw @a` with the dashboard user's
  name, so it reads `[Dashboard] Sam: hi`). Available to friend accounts (FEAT-10) with FIX-05
  validation.
- Colour by level (warn/error) using existing status tokens.

## Implementation notes
- `webapp/websocket_manager.py`: line sequence numbers so the polling fallback can ask for lines
  after `n`. `GET /api/console?since=n` → `{lines, last}`.
- `dashboard.js`: `ConsoleView` with append-only rendering (cap DOM at 2000 lines), filter on
  render, `textContent` only.
- Tests: `since` returns only newer lines; WS with cookie; chat message escaping (no newlines;
  `tellraw` JSON built with `json.dumps`, never string concatenation).

## Acceptance criteria
- New console lines appear within ~0.5 s with no polling traffic while the socket is open.
- Pressing ↑ in the command box recalls the previous command.
