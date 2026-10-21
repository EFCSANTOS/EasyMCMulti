# Testing

## Layout
`tests/test_webapp_smoke.py`; sets env vars at import; `TestClient(create_app())`.
Run: `python -m pytest -q tests/test_webapp_smoke.py`.

## Covered
- `/health` → `{status:"ok"}`.
- `/` dashboard content; `/dashboard/{7 sections}`; auth defaults in HTML.
- `/api/console`, `/api/status` (401 unauth, 200 auth with keys).
- `/api/modrinth/install` forwards `project_id`.
- `/api/backups`, `/api/mods` keys.
- `/api/admin/{9 actions}` status ∈ {200,409,400}.
- `/api/playit/status` keys; `/api/playit/start` (monkeypatched) → running; agent name per platform.
- `/api/system/metrics` bounds.
- `PUT /api/config` round-trip.
- `/api/versions/vanilla` shape.
- WS: unauth rejected; auth streams.
- Start-before-install ordering (monkeypatched); property mapping; lifecycle start→command→stop.

## Gaps
- No direct tests for `auth.py` helpers, `websocket_manager.py` edge cases, `state.snapshot` locking.
- No tests for backup create/list/restore, mod upload, Modrinth search, PlayIt configure/stop.
- No `core/` unit tests in this file (process/config/downloaders/java/playit).
- No real downloads/Java/PlayIt/Modrinth.
- `test_dashboard_uses_runtime_auth_defaults` asserts `value="admin"` is rendered — codifies a
  security smell rather than preventing it.
- No CI config inspected.