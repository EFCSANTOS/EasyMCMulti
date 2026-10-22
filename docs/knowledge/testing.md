# Testing

## Layout
`tests/test_webapp_smoke.py`; sets env vars at import; `TestClient(create_app())`.
Run: `.venv\Scripts\python.exe -m pytest -q tests/test_webapp_smoke.py`. Last run 2026-10-22:
76 passed (~15 s, `.venv` Python 3.14.7; the updater tests create real git repos, so `git` must be
on PATH). Dev deps: `pip install -r requirements-dev.txt`. No `conftest.py`, `pytest.ini` or CI workflow exists.

## Data isolation
Autouse fixture `isolated_data_dir` redirects `paths.DATA_DIR/JAVA_DIR/SERVERS_DIR`,
`config_store._FILE` and `playit._CONFIG_FILE` to `tmp_path`, so tests no longer touch the real
`EZ_Server_Data/`. Any new module-level path constant derived from `paths.DATA_DIR` must be added to
this fixture. (Before 2026-10-22 the suite overwrote the real `launcher_config.json` and
`playit_config.json` — a real install may still contain leftovers such as `motd: "Advanced test"`,
`server_port: 25566`, or a `demo-secret` PlayIt config.)

## Hidden dependencies
- **Network**: `/api/status` calls `api.ipify.org` (10 s timeout; `"unavailable"` on failure; cached
  5 min per process); `/api/versions/vanilla` hits Mojang (falls back to `FALLBACK_VERSIONS`).
- **Order-dependent**: one module-level `app`/`client` and shared `app.state.runtime`.
  `test_server_lifecycle_start_and_stop` relies on the `DummyProcess` left in `runtime.process` by the
  previous test (start → "already running"). Run alone, it would perform a real Java/server download
  and launch (into the temp data dir).
- `test_admin_action_routes_exist_for_dashboard_buttons` runs before any start, so it only exercises
  the 409 branch (command mapping is untested).

## Covered
- `/health` → `{status:"ok"}`.
- `/` dashboard content; `/dashboard/{7 sections}`; `#action-status` and server controls on every
  page; each feature's controls present on its page; Advanced page save button; no credentials in HTML.
- **JS ↔ API contract**: `test_dashboard_js_only_calls_existing_api_routes` regex-extracts every
  `/api/...` literal in `dashboard.js` and asserts a matching route exists (templated URLs match by
  prefix).
- `/api/console`, `/api/status` (401 unauth, 200 auth with keys).
- `/api/modrinth/install` forwards `project_id`; mod upload → 400 for vanilla.
- `/api/backups`, `/api/mods` keys.
- `/api/admin/{9 actions}` status ∈ {200,409,400}.
- `/api/playit/status` keys and `configured` (not `running`) without an agent; `/api/playit/start`
  (monkeypatched) → running; agent name per platform.
- `/api/system/metrics` bounds.
- `PUT /api/config` round-trip.
- `/api/versions/vanilla` shape.
- WS: unauth rejected; auth streams.
- Start-before-install ordering (monkeypatched); property mapping; lifecycle start→command→stop;
  restart waits for the old process to stop before starting a new one.
- Player tracking from join/leave lines (incl. chat-spoof rejection).
- Backup list + restore round trip; restore rejects zip-slip archives and paths outside the server dir.
- `/api/playit/config` never returns the secret; wrong password → 401.
- PlayIt (stubbed via `fake_playit` fixture + `DummyAgent`): start, async configure returning the
  claim URL and finishing in the background, `reset` forgetting the account.
- Remote dashboard (`fake_cloudflared` fixture + `FakeQuickTunnel`, `WEB_TUNNEL_RETRY_SEC` = 0):
  toggle on/off, restart after unexpected exit, missing-binary error; `QuickTunnel._reader` announces
  the URL only after registration; URL parsing and release asset names. A live check against a
  throwaway local server lives outside the suite (needs internet).
- Alerts (`sent_emails` fixture stubs `notifier.send_email` and enables email): crash mail + 10-min
  throttle, DOWN mail at the crash limit, one mail per new dashboard link (+ "went down"), password
  never returned and kept when blank, test endpoint (400 / heartbeat ping), PlayIt agent crash →
  restart + mail while a deliberate stop doesn't restart, `remote_dashboard` default on.
  `isolated_data_dir` also redirects `notifier._CONFIG_FILE`. Tests using autostart must include
  `fake_cloudflared` (the link is on by default and would otherwise download cloudflared).
- Self-update (`git_repos` fixture: bare origin + publisher clone + app clone in `tmp_path`, with
  `updater.REPO_DIR` patched): fast-forward to new commits, refusal on local changes (full file
  names), package reinstall and rollback on pip failure, "not a git clone"; app flow with
  `updater.apply`/`restart_process` stubbed: shutdown before restart, resume marker restarts the
  server on the next `autostart()`, failure reported; assets cache-busted.
- Not covered: the real `restart_process` (exec / Windows relaunch) — would kill the test process.
- Worlds: zip import (nested singleplayer save, Paper dimension siblings, no-`level.dat` and
  zip-slip rejection with cleanup), endpoints create → activate (writes `level_name`/`level_seed`,
  `level-name` property) → rename → delete (active refused), upload/download round trip, switching
  while running restarts the server, backups of the active world + pre-restore rename.
- Mod browser (`fake_modrinth` fixture fakes `modrinth._get` and `download`): browse without query
  (sort + server-side facets), categories filter, install with a required dependency (optional
  skipped), installed `project_ids`, details plain text, remove, vanilla 400. A live read-only
  check against api.modrinth.com was run manually (2026-10-22), outside the suite.
- Platform: system Java selection, Termux missing-Java hint, `unix_args.txt` vs `win_args.txt`,
  metrics with blocked CPU counters, `termux-chroot` agent wrapping.
- Always-on (`fake_server` fixture + `RecordingProcess`, whose `exit(code)` fires `on_exit`;
  `CRASH_RESTART_DELAY_SEC` patched to 0): crash → restart, limit → `crashed`, Stop / console
  `stop` are not crashes, `auto_restart` off, autostart server + PlayIt, `EZ_NO_AUTOSTART`, shutdown
  stops server and agent, Always-on switches render.
- Not covered: `termux-setup.sh` / `run.sh` / `ezmc.sh` (only `bash -n`), runit/Termux:Boot,
  real PlayIt API, real Android device.

## Gaps
- No direct tests for `auth.py` helpers, `websocket_manager.py` edge cases, `state.snapshot` locking.
- No tests for backup create, Modrinth search, PlayIt configure/stop, restart kill-on-timeout.
- No `core/` unit tests (process/config/downloaders/java/playit).
- No real downloads/Java/PlayIt/Modrinth.
- No browser-level frontend tests (JS behavior is only syntax-checked and contract-checked).
