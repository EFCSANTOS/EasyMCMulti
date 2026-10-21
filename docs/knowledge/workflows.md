# Workflows

## 1. Authentication
`webapp/auth.py::validate_auth_settings` runs at `create_app()`; missing env vars → `RuntimeError`.
REST routes use `require_auth` (HTTPBasic). WebSocket `/ws/console` uses
`verify_basic_auth_header` (manual Base64 decode; scheme must be `basic`, `user:pass` required).
*(Exact route wiring not captured — verification.md.)*

## 2. Start server (verified via `test_server_start_installs_before_launch`)
1. Effective config merged (`_effective_config`).
2. `_server_is_installed(target_dir, loader)` — if false:
   `downloaders.prepare(loader, version, target_dir, java_exe, log, progress)` with progress callback
   → `update_install_state`.
3. `java_manager.ensure_java(required)` → java executable path.
4. `downloaders.launch_args(...)` → argv list.
5. `update_properties(server_dir, **kwargs)` from request body.
6. `accept_eula(server_dir)` (eula=True).
7. `ServerProcess(args, cwd, on_output, on_exit).start()`.
8. `runtime.process` set; state transitions observed by tests: `"starting"`/`"running"`.

## 3. Stop / command
- `ServerProcess.send(cmd)` writes `cmd\n` to stdin if running.
- `ServerProcess.stop()` sends `"stop"` (graceful, saves world).
- `kill()` force-kills.
- `/api/server/command` test sends `{"command":"say hello"}` and expects 200.

## 4. Console streaming
- `ServerProcess._reader` thread → `on_output(line)` → `console_manager.publish(line)`.
- `publish` appends to `history` (2000) and enqueues to each client (250 max; oldest dropped).
- `connect()` replays last 500 history lines.
- `disconnect()` drains queue and cancels sender task.
- Tests: `/api/console` returns `{"lines": [...]}`; WS requires auth; WS streams published line.

## 5. Config persistence
- `PUT /api/config` with `{loader, version, ram_mb}` → 200, echoes values (`test_config_can_persist_values`).
- Persisted via `core/config_store` (load/save not inspected).

## 6. Versions
- `GET /api/versions/{loader}` → `{loader, versions}`. `FALLBACK_VERSIONS` used when remote list fails
  (inference from constant name — verification.md).

## 7. PlayIt tunnel
- `GET /api/playit/status` → `{configured, state, address}`.
- `POST /api/playit/start`: `load_config` → `ensure_agent` → `ensure_tunnel(secret, local_port, ...)`
  → `PlayitAgent(secret).start()`; state `"running"`, address returned.
- Agent binary name is platform-aware: `playit-{linux|windows|darwin}-{arch}` via
  `playit._agent_binary_name()` (tested for linux amd64/aarch64/armv7).
- `_playit_on_exit` resets state and publishes exit line.

## 8. Mods (Modrinth)
- `POST /api/modrinth/install` body `{"project_id": "..."}` → `modrinth.install(identifier, loader, version, target_folder, log)`
  → `{filename}`.
- `GET /api/mods` → `{mods}`. `GET /api/backups` → `{backups}` (tests only assert keys).

## 9. Admin actions
- `POST /api/admin/{op|deop|kick|ban|day|night|clear|rain|save}` body `{"player": "..."}`
  → 200/409/400 (test only checks status set).