# Testing

## Layout
Since QUAL-02 (2026-10-23): `tests/support.py` (env vars, one `app = create_app()` + `client`,
helpers like `_start`/`_wait_idle`/`_login`, constants like `AUTH`, and all shared fixtures;
`__all__` exports underscore helpers too), `tests/conftest.py` (re-exports the fixtures; autouse
`no_network` and `fresh_runtime`), and one file per area: `test_pages`, `test_auth`, `test_console`,
`test_config`, `test_lifecycle`, `test_diagnosis`, `test_backups`, `test_worlds`, `test_mods`,
`test_players`, `test_remote`, `test_alerts`, `test_updater`, `test_platform`, plus `test_core`
(unit tests of `core/`). Each test file starts with `from support import *`. Real-format log
snippets: `tests/fixtures/logs/`. `pytest.ini` sets `testpaths` and the `network` marker.
Run: `.venv\Scripts\python.exe -m pytest -q` — random order (`pytest-randomly`; reproduce with
`-p randomly --randomly-seed=N`, disable with `-p no:randomly`). Last run 2026-10-23: 235 passed
(~30 s; the updater tests create real git repos, so `git` must be on PATH; every file also passes
on its own). Dev deps: `pip install -r requirements-dev.txt`. CI: `.github/workflows/tests.yml`
(Ubuntu + Windows, Python 3.13: pytest, `node --check` on the dashboard scripts, `bash -n` on
the shell scripts) on every push/PR to development, release and main.

## Patching
Routers use shared helpers as `common.NAME`, so monkeypatch `common_module` (`webapp.common`) for
`_effective_config`, `_server_is_installed`, `ServerProcess`, `update_properties`, `accept_eula`,
`psutil`, timeouts (`CRASH_RESTART_DELAY_SEC`, `PLAYIT_RETRY_SEC`, `WEB_TUNNEL_RETRY_SEC`) and
`_host_ram_mb`; core functions are patched on their `core` module (`downloaders.prepare`, …).

## Isolation (autouse fixtures)
- `no_network` (conftest): `socket.connect`/`getaddrinfo` fail fast for anything but loopback, so
  `public_ip()` → "unavailable", versions → `FALLBACK_VERSIONS`, Modrinth/Mojang/Discord calls must
  be faked. Mark a test `@pytest.mark.network` to allow real access.
- `fresh_runtime` (conftest): every test starts from a new `RuntimeState` (copied over the shared
  object, which the routers' closures hold) and `services.reset_notifications()` (alert throttles,
  pending friend join/leave batches) — no test depends on what an earlier one left.
- `isolated_data_dir` redirects `paths.DATA_DIR/JAVA_DIR/SERVERS_DIR`,
`config_store._FILE` and `playit._CONFIG_FILE` to `tmp_path`, so tests no longer touch the real
`EZ_Server_Data/`. Any new module-level path constant derived from `paths.DATA_DIR` must be added to
this fixture. It also clears `app.state.sessions`, `app.state.login_limiter` and the shared
client's cookies, so a lockout or login in one test never leaks into the next; tests that need a
browser session use their own `TestClient(app)` (optionally `client=(ip, port)` to pick the peer
IP). (Before 2026-10-22 the suite overwrote the real `launcher_config.json` and
`playit_config.json` — a real install may still contain leftovers such as `motd: "Advanced test"`,
`server_port: 25566`, or a `demo-secret` PlayIt config.)

## Notes
- Background timers (crash/tunnel retries) can outlive their test and fire in a later one: assert
  the outcome ("a new tunnel replaced the crashed one"), not exact instance counts. CI prints the
  `--randomly-seed` in its header to reproduce an order-dependent failure.
- One shared `app`/`client` per session (cheap and keeps the `common_module` patch targets simple);
  isolation comes from the autouse fixtures above.
- `test_admin_action_routes_exist_for_dashboard_buttons` exercises only the 409 branch (no server);
  the command mapping is covered by the FIX-05 tests with a fake server.

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
- Device health (`test_health.py`): temperatures from fake `/sys` zones + battery and from
  `termux-battery-status` (cached); metrics include them; CPU alert only after N minutes, once, margin
  before "back to normal"; RAM and heat (device + battery) alerts; per-alert channels (email only /
  Discord only) and off switches; settings validation; page markup.
- Bedrock (FEAT-18, `test_bedrock.py`): Geyser config edit (old `remote:` / new `java:` layouts,
  idempotent, missing file); Bedrock tunnel request is `minecraft-bedrock` over UDP; PlayIt config keeps
  the Bedrock address; switch installs/removes (Floodgate for Paper via GeyserMC); address on invite and
  status page; a refused Bedrock tunnel doesn't stop the Java one; `.`-prefixed whitelist names.
- Named tunnel (FEAT-17, `test_remote.py`): `NamedTunnel` command line with the token only in the
  environment; named mode gives the same address on each start (fake tunnel); settings validation.
- App manifest (FEAT-16, `test_pages.py`): content type, standalone + start URL, icon sizes match the
  PNG headers, unknown icon names 404, every page links the manifest.
- Friends (FEAT-10, `test_users.py`): table-driven 403 over `app.routes` for a friend session
  (`FRIEND_ROUTES` lists the deliberate exceptions); friend start/chat/backup and stop gating;
  account create/reset/remove with sign-out; owner env account; friend WS refused; hashes only.
- Migration (FEAT-09, `test_migrate.py`, `fabric_1_21_1` fixture fakes Modrinth and downloads):
  plan (worlds, files, compatible/missing mods, downgrade, loader change); the full move; missing
  mods/downgrade/running → 409; existing target worlds kept aside.
- Storage (FEAT-15, `storage_tree` fixture): breakdown sizes and flags, Java need from server
  versions; deletes refused for the active dir, backups, unknown ids and while running; low-space
  alert once until recovered.
- Order independence (QUAL-02): random order found two hidden dependencies — the alert throttle and
  a pending friends batch surviving between tests — fixed by `fresh_runtime`; the earlier one-off
  failure was most likely one of these.
- `core/` units (`test_core.py`): `required_java` table, version sorting and NeoForge mapping,
  `update_properties` round trip, `import_zip` (world root, dimensions, escaping members), `netinfo`
  fallbacks without network.
- Identity (FEAT-14): icon checks from raw PNG headers (size, type, 64 KB); extra properties round
  trip, managed/locked/bad keys and multi-line values → 422, null removes; MOTD `§` written as
  `\u00a7` with the owner's `\n` intact; `MANAGED` equals the Settings form's keys.
- Performance (FEAT-13): flag variants (phone, > 12 GB), launch args with the switch on/off and extra
  args appended; extra-args validation (`-jar`, `@file`, newline, bare words, bad quoting → 422);
  host info endpoint and the Performance card render.
- Activity (FEAT-12): playtime/sessions/online from a synthetic join/leave/stop stream (plus a
  half-written line), hour averages; compaction keeps totals; the server output and a crash feed the log.
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
  Fabric/Forge/NeoForge; friends download it signed out from the status page. Single jars: list without
  the server-only mod, one-by-one download, 404 for server-only/unknown/traversal names, auth required,
  status-page variant.
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
- Player transfer (`test_player_transfer.py`): NBT round trip (every tag type, modified UTF-8),
  player list + mod-data candidates, move to spawn (old and 1.21.9+ `level.dat` spawn) keeping
  inventory / `ForgeCaps`, chosen players / parts / mod data, refusals that leave the target
  untouched, endpoints (backup first, no backup for a bad request, 409 while running).
- Mod browser (`fake_modrinth` fixture fakes `modrinth._get` and `download`): browse without query
  (sort + server-side facets), categories filter, install with a required dependency (optional
  skipped), installed `project_ids`, details plain text, remove, vanilla 400. A live read-only
  check against api.modrinth.com was run manually (2026-10-22), outside the suite.
- Platform: system Java selection, Termux missing-Java hint, `unix_args.txt` vs `win_args.txt`,
  metrics with blocked CPU counters, CPU from `/proc/stat` without psutil and from own `/proc/<pid>/stat` (Android), `termux-chroot` agent wrapping.
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
