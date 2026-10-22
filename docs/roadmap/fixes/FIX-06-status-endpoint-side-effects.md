# FIX-06 — Make `GET /api/status` a pure read

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Fix (design) | P2 | S | — |

## Why
`/api/status` is polled every 5 seconds by every open dashboard tab, and it writes to
`RuntimeState` (loader, version, LAN/public address, PlayIt fields) on each call. That makes
behaviour depend on whether someone happens to have the dashboard open, hides bugs (state looks
right only after a poll), and races with lifecycle operations (FIX-04). It is listed as open
conflict #1 in verification.md.

## Current state
- workflows.md §9: "refresh loader/version from config; `netinfo.lan_ip()` / `public_ip()`;
  `_playit_state` …" — all assigned into `runtime` inside the GET handler.
- `public_ip()` is cached for 5 minutes, so the network cost is already bounded.

## Proposal
- Compute the response from `runtime.snapshot()` **plus** derived values (config loader/version,
  addresses, PlayIt state) merged into the returned dict, without assigning them to `runtime`.
- Where something really must be stored (e.g. loader/version after a config save), update
  `runtime` at the moment of change: in `PUT /api/config`, `launch_server`, PlayIt start/stop.

## Implementation notes
- `webapp/app.py`: split into `_status_payload(config) -> dict` (pure) and keep the route a thin
  wrapper. Check other readers of `runtime.loader` / `runtime.lan_address` still get fresh values.
- Test: two consecutive GETs with a changed config in between return the new values while
  `runtime` fields are untouched by the GET.

## Acceptance criteria
- No `runtime.<field> =` assignment remains in the status handler.
- The dashboard looks the same.
