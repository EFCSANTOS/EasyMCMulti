# FIX-01 — Login sessions and failed-login lockout

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Fix (security) | P0 | M | — |

## Why
The remote dashboard link is **on by default**, so every install is reachable from the internet by
anyone who gets the link (a screenshot in a group chat is enough). Today a wrong password costs
nothing, so the password can be guessed without limit. This is already the project's top
"Must fix" (`docs/knowledge/verification.md`).

## Current state
- `webapp/auth.py::verify_credentials` — HTTP Basic, constant-time compare, no rate limit.
- `webapp/templates/login.html` probes `GET /api/status` and stores the username and password in
  `sessionStorage`; `dashboard.js::buildBasicAuth` re-sends them on every request.
- Sign-out clears `sessionStorage` only; there is no server-side session.
- Side effect: browsers can't put Basic auth on a WebSocket, so the GUI polls the console instead
  of using `/ws/console` (decisions.md #19).

## Proposal
1. **Sessions.** `POST /api/login {username, password}` → on success set a cookie
   `ezmc_session=<secrets.token_urlsafe(32)>` with `HttpOnly`, `SameSite=Strict`, `Path=/`, and
   `Secure` when the request came through the tunnel (`X-Forwarded-Proto: https` /
   `CF-Connecting-IP` present). Sessions live in memory: `{token: (username, expires_at)}`, 7-day
   sliding expiry. A dashboard restart logs everyone out — acceptable for a friends server.
2. **Logout.** `POST /api/logout` deletes the session and clears the cookie.
3. **Lockout.** Per client IP (`CF-Connecting-IP` when present, else `request.client.host`):
   after 5 failures in 15 minutes, refuse logins from that IP for 15 minutes (429 + `Retry-After`).
   Also a global ceiling (e.g. 30 failures/15 min) so rotating IPs can't brute-force either.
   Send a `security` alert (FEAT-05 / existing email) on the first lockout.
4. **Password strength.** At startup, if `remote_dashboard` is enabled and the password is shorter
   than 12 characters, log a loud warning and show a banner on the Remote page (refusing to start
   would brick existing installs; `termux-setup.sh` already enforces 12+).
5. **Keep Basic auth for scripts** (curl, the WS test) behind the same lockout counter, so the API
   stays scriptable.
6. **CSRF.** With `SameSite=Strict` cookies and JSON bodies, cross-site form posts can't
   authenticate; additionally require `Content-Type: application/json` (or multipart for uploads) on
   state-changing routes.

## Implementation notes
- `webapp/auth.py`: `SessionStore` and `LoginLimiter` classes (thread-safe with a `Lock`; prune on
  access). `require_auth` accepts a valid cookie **or** valid Basic credentials.
- `webapp/templates/login.html`: post to `/api/login`; stop writing `sessionStorage`.
- `dashboard.js`: drop `buildBasicAuth`; `fetchJson` relies on the cookie (`credentials:
  'same-origin'`); on 401 redirect to `/login`.
- `/ws/console`: also accept the session cookie (cookies are sent on WebSocket upgrades) — unlocks
  FEAT-11.
- Tests: login success sets cookie; wrong password ×5 → 429; lockout expiry (patch the clock);
  logout invalidates; WS with cookie streams; `test_dashboard_does_not_embed_credentials` still passes.
- Remove the `MUST FIX` comment in `auth.py` and the "Must fix" section in verification.md.

## Acceptance criteria
- No credential is stored in browser storage; the password crosses the network once per login.
- Six rapid wrong passwords from one IP get 429; the right password is still refused until the
  window passes.
- Existing scripted Basic-auth clients keep working.

## Keep it small
No user database (that's FEAT-10), no 2FA, no "remember this device" — the 7-day cookie is it.
