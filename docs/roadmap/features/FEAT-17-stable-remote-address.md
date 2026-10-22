# FEAT-17 — Stable remote dashboard address

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P3 | M | FIX-01 |

## Why
The Cloudflare quick-tunnel link changes on every restart, so bookmarks, the installed phone app
(FEAT-16) and the friends' status-page link (FEAT-03) break after each reboot or update. The
"new link" email helps but is a workaround.

## Current state
- `core/cloudflared.py::QuickTunnel` runs `cloudflared tunnel --url http://127.0.0.1:<port>` (no
  account, random `*.trycloudflare.com` URL, no uptime guarantee). decisions.md #21.

## Proposal
Keep quick tunnels as the zero-setup default and add two optional modes on the Remote page:
1. **Cloudflare named tunnel (own domain):** the owner creates a tunnel in the Cloudflare dashboard
   (free plan, needs a domain on Cloudflare), pastes the **tunnel token**; the app runs
   `cloudflared tunnel --no-autoupdate run --token <token>`. Stable `https://mc.example.com`.
   Optionally the owner adds Cloudflare Access in front (email one-time codes) for a second layer
   of login — documented, not built.
2. **Private network (Tailscale / ZeroTier) — documentation only:** for groups that prefer nothing
   public at all, a README section explaining how to use the dashboard (and even the Minecraft
   port, without PlayIt) over a tailnet. The app just needs to listen on that interface, which it
   already does with `--host 0.0.0.0`.

## Implementation notes
- `core/cloudflared.py`: `NamedTunnel` alongside `QuickTunnel` (no URL parsing — the URL is the
  owner-entered hostname; "connected" detected from the registration line, like today).
- Token stored like other secrets (never returned; `token_set` boolean), atomic write (FIX-03).
- Config key `remote_mode`: `quick` (default) | `named`; `remote_hostname` for display.
- Tests: mode switch starts the right command line; token not in API responses.

## Acceptance criteria
- With a named tunnel configured, the dashboard address is the same after a restart.

## Keep it small
No automatic Cloudflare API provisioning (creating tunnels/DNS via API needs an account token with
broad permissions — too much power for this app to hold).
