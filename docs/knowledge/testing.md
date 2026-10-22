# Testing

## Layout
`tests/test_webapp_smoke.py` (+ `tests/fixtures/logs/` for FEAT-08); sets env vars at import; `TestClient(create_app())`.
Run: `.venv\Scripts\python.exe -m pytest -q tests/test_webapp_smoke.py`. Last run 2026-10-22:
176 passed (~33 s, `.venv` Python 3.14.7; the updater tests create real git repos, so `git` must be
on PATH). Dev deps: `pip install -r requirements-dev.txt`. No `conftest.py`, `pytest.ini` or CI workflow exists.

## Patching
Routers use shared helpers as `common.NAME`, so monkeypatch `common_module` (`webapp.common`) for
`_effective_config`, `_server_is_installed`, `ServerProcess`, `update_properties`, `accept_eula`,
`psutil`, timeouts (`CRASH_RESTART_DELAY_SEC`, `PLAYIT_RETRY_SEC`, `WEB_TUNNEL_RETRY_SEC`) and
`_host_ram_mb`; core functions are patched on their `core` module (`downloaders.prepare`, …).

## Data isolation
Autouse fixture `isolated_data_dir` redirects `paths.DATA_DIR/JAVA_DIR/SERVERS_DIR`,
`config_store._FILE` and `playit._CONFIG_FILE` to `tmp_path`, so tests no longer touch the real
`EZ_Server_Data/`. Any new module-level path constant derived from `paths.DATA_DIR` must be added to
this fixture. It also clears `app.state.sessions`, `app.state.login_limiter` and the shared
client's cookies, so a lockout or login in one test never leaks into the next; tests that need a
browser session use their own `TestClient(app)` (optionally `client=(ip, port)` to pick the peer
IP). (Before 2026-10-22 the suite overwrote the real `launcher_config.json` and
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
  `/api/...` literal in every script of `layout.SCRIPTS` (`_dashboard_js()`) and asserts a matching route exists (templated URLs match by
  prefix).
- `/api/console`, `/api/status` (401 unauth, 200 auth with keys).
- Login (FIX-01): cookie flags and cookie auth, lockout after 5 failures (also for the right
  password and Basic auth), expiry with a patched `clock`, per-IP isolation, spoofed vs loopback
  `CF-Connecting-IP`, global ceiling, one security alert per lockout, logout, sliding session
  expiry, CSRF (non-JSON / cross-origin → 403), WS with cookie, `weak_password` flag.
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
- Live console (FEAT-11): `?since=` returns only newer lines; chat builds valid `tellraw` JSON even
  with quotes/backslashes, 409 when stopped, one-line/length validation; filter and chat controls render.
- Pure status (FIX-06): two GETs around a config change return the new loader/version and addresses
  while the `runtime` fields stay untouched; the handler source has no `runtime.x =` assignment.
- Mod updates (FEAT-07, `outdated_mods` fixture fakes `mod_updates.requests.post`/`download` and
  `modrinth.install`): one bulk request finds two updates; update replaces the jar, keeps `.old`,
  updates the record, installs the dependency, backs up first; undo restores; a corrupted download
  changes nothing; 409 while running; `.old` removed after 5 min of running; vanilla → 400.
- Diagnosis (FEAT-08): every rule against a real-format snippet in `tests/fixtures/logs/` (add a
  fixture there for each new rule); crash → stored diagnosis + "Likely cause" in the alert, cleared on
  start; EULA fix; kill only Java (fake psutil); log list/view (truncation, .gz), download, traversal.
- Schedule (FEAT-06, `scheduler_tick(datetime)` with fixed times): warnings at 10/5/1 then one
  restart a day; a late joiner hears the real minutes left; no restart when >30 min late; idle stop
  only after N empty minutes, cancelled by a join, `stopped_reason` shown and cleared, friends told
  once; quiet hours wrap midnight and block autostart; time/minute validation.
- Backups (FEAT-01): name parsing (old and labelled); retention picks the right set and spares
  manual backups; `backup_tick` with a patched clock only backs up a changing world and sends
  `save-off`/`save-all flush`/`save-on`; manual vs safety labels and pruning; low disk → 500 +
  alert; deleting a world makes an `auto-pre-delete` backup and is refused when it fails; config ranges.
- Dashboard scripts: no top-level name defined twice across the shared-scope scripts.
- Discord (FEAT-05, `discord_posts` fixture stubs `notifier.send_discord`, batch window 0.05 s):
  webhook URLs write-only and validated; owner alerts only to the owner hook; friends hook gets
  online (with address)/offline/joins and never a trycloudflare link; status page link to friends;
  join+leave in one batch says nothing; switches; 429 retry; test button posts to both.
- Mod pack (FEAT-04, `fabric_server` fixture fakes `modpack._lookup`/`_projects`): index format,
  hashes and env; server-only mod excluded; unknown jar in `overrides/mods/`; cached until a jar
  changes; Modrinth down → all bundled; Paper/empty → 400; loader version detection for
  Fabric/Forge/NeoForge; friends download it signed out from the status page.
- Status page (FEAT-03, `offline_network` fixture fakes `netinfo.public_ip`/`lan_ip`): off by
  default (404, owner API needs auth); only public fields and no secrets/emails/LAN IP/token in page
  or JSON; show-LAN/show-names switches; PlayIt address preferred, public IP fallback; regenerate and
  disable kill the old link; token refused by `PUT /api/config`; invite text with mods required;
  new-link email carries the status link.
- Players (FEAT-02): stopped-server whitelist edit writes the offline UUID (Notch →
  `b50ad385-…`); online mode resolves through a faked Mojang API (unknown → 400); running server
  sends the right commands (`SYNC_WAIT_SEC` patched small); bad names/levels/IPs/reasons → 400;
  409 while a lifecycle job holds the lock; refused joins land in "Recently seen".
- Input validation (FIX-05): bad player names / multi-line or over-long commands → 400 and nothing
  sent; each bad config field → 422 naming it, unknown key → 422, nothing saved; `ram_mb` above host
  RAM (`_host_ram_mb` patched) → 422; start payload validated; stored garbage → defaults + one console
  warning, and start still works.
- Atomic writes (FIX-03): `.bak` kept and no `.tmp` left for every settings file; a truncated
  `launcher_config.json` is restored from `.bak`, repaired on disk and logged; a failed replace
  leaves the original intact; `PUT /api/config` → 500 on a disk error. Patch inside
  `monkeypatch.context()` — `monkeypatch.undo()` would also undo the data-dir redirect.
- Lifecycle jobs (FIX-02/04): create returns 202 within 2 s and installs on the thread (gated fake
  `prepare`); every lifecycle route → 409 "Busy" while one runs; 4 concurrent starts → one 202 and
  one process; a failed install → `install_status="failed"` + `operation_error` + console line; the
  crash-restart path waits for the lock and doesn't double-start. Helpers `_start()` and
  `_wait_idle()` wait for the background job — use them instead of a bare start POST.
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
