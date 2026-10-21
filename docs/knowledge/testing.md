# Testing

## Layout
`tests/test_webapp_smoke.py`; sets env vars at import; `TestClient(create_app())`.
Run: `python -m pytest -q tests/test_webapp_smoke.py`. Last run 2026-10-22: 22 passed (~1 s,
`.venv` Python 3.14.7). No `conftest.py`, `pytest.ini` or CI workflow exists in the repo.

## Side effects & hidden dependencies
- **Writes real user data**: `PUT /api/config` and `POST /api/server/start` persist to
  `EZ_Server_Data/launcher_config.json` (`test_server_start_installs_before_launch` leaves
  `server_port: 25566`, `motd: "Advanced test"`, `max_players: 12`). Back up or isolate
  `paths.DATA_DIR` before running against a real install.
- **Network**: `/api/status` calls `api.ipify.org` (10 s timeout; `"indisponible"` on failure);
  `/api/versions/vanilla` hits Mojang (falls back to `FALLBACK_VERSIONS`).
- **Order-dependent**: one module-level `app`/`client`. `test_server_lifecycle_start_and_stop` relies
  on the `DummyProcess` left in `runtime.process` by the previous test (start → "already running").
  Run alone, it would perform a real Java/server download and launch.
- `test_admin_action_routes_exist_for_dashboard_buttons` runs before any start, so it only exercises
  the 409 branch (command mapping is untested).

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
- No frontend tests; the JS/API mismatches in verification.md are uncaught.
- No tests for `/api/playit/config` secret exposure or restore path validation.