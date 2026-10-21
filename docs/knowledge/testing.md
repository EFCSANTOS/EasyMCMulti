# Testing

## Layout
- Single smoke suite: `tests/test_webapp_smoke.py`. Sets `EZ_WEBAPP_USERNAME/PASSWORD` at import,
  builds `TestClient(create_app())`.
- Run: `python -m pytest -q tests/test_webapp_smoke.py`.

## Covered (verified by test names/asserts)
- `/health` → `{status:"ok"}`.
- `/` dashboard HTML (mentions "EZ Minecraft Server", "Dashboard", "PlayIt",
  "Restart server", "Backup world", "Mods / Plugins", "Admin commands").
- `/dashboard/{section}` for 7 sections.
- Auth defaults reflected in dashboard (monkeypatched env).
- `/api/console` history; `/api/status` requires auth (401) and returns state keys.
- `/api/modrinth/install` forwards `project_id` as identifier (monkeypatched `modrinth.install`).
- `/api/backups`, `/api/mods` key presence.
- `/api/admin/{9 actions}` status ∈ {200,409,400}.
- `/api/playit/status` keys; `/api/playit/start` monkeypatched to "running"; agent name per platform.
- `/api/system/metrics` cpu ∈ [0,100], ram ≥ 0, ramTotal > 0.
- `PUT /api/config` round-trip.
- `GET /api/versions/vanilla` shape.
- WS `/ws/console`: unauthenticated rejected; authenticated streams.
- Start-before-install ordering with monkeypatched `downloaders.prepare`, `java_manager.ensure_java`,
  `launch_args`, `ServerProcess`, `update_properties`; asserts `server-port`/`motd`/`max-players`.
- Lifecycle start → command → stop.

## Gaps / limitations
- No unit tests for `core/server_process.py`, `core/config_store.py`, `core/downloaders.py`,
  `core/java_manager.py`, `core/playit.py` in this file.
- No coverage for real downloads, real Java, real PlayIt, real Modrinth.
- No test for `verify_basic_auth_header` directly.
- `test_dashboard_uses_runtime_auth_defaults` asserts `value="admin"` in HTML — implies dashboard
  embeds credentials in the page (see verification.md; security concern).
- No CI config inspected.