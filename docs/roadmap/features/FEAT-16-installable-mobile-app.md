# FEAT-16 — Installable phone app (PWA)

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P3 | S | — (FIX-01 recommended so the login survives app restarts) |

## Why
Owner and friends mostly check the server from their phones. "Add to home screen" with a proper
icon, full-screen view and no browser bar makes the dashboard feel like an app, for almost no code.

## Current state
- Responsive dashboard (verified at 390 px), dark theme, no web app manifest, no app icon.

## Proposal
- `GET /manifest.webmanifest`: name, short name ("EZ MC"), `display: standalone`, theme/background
  colours from the dashboard tokens, `start_url: /dashboard/overview`, 192/512 px icons (and a
  maskable variant).
- `<link rel="manifest">`, `theme-color` meta and `apple-touch-icon` on every page and `/login`.
- A minimal service worker only if a target browser still needs one for the install prompt; it
  must **not** cache API responses (stale server state is worse than a network error). Cache only
  the static shell, versioned by `ASSET_VERSION`.
- Note in the UI: installing requires HTTPS, so it works through the Cloudflare link (and
  `localhost`) but not on plain-HTTP LAN addresses.

## Implementation notes
- Icons: simple SVG source in `webapp/static/icons/`, PNGs exported once and committed.
- Routes are static and unauthenticated like the other shells (decisions.md).
- Tests: manifest served with the right content type; every page links it.

## Acceptance criteria
- On Android Chrome via the dashboard link, "Install app" adds an icon that opens the dashboard
  full-screen.

## Keep it small
No push notifications (Discord/email already cover alerts), no offline mode.
