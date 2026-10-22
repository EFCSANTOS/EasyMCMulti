# Data Model

No DB, no migrations. Files + in-memory `RuntimeState`.

## Data root (`core/paths.py`)
`EZ_Server_Data/` next to the PyInstaller exe (`sys.frozen`) or at repo root; git-ignored.
```
EZ_Server_Data/
  launcher_config.json     config profile (config_store._FILE)
  playit_config.json       {"secret", "address"} (playit._CONFIG_FILE) — plaintext secret
  cloudflared-<os>-<arch>  Cloudflare tunnel binary, downloaded on Windows/Linux (cloudflared.ensure_binary)
  playit-<os>-<arch>[.exe] PlayIt agent binary (playit._agent_path)
  java/jre-<feature>/      Temurin JRE per Java major (java_manager._install_dir)
  servers/<loader>-<version>/  one dir per loader+version; spaces → "_" (paths.server_dir)
```
Switching loader/version in config switches to a different server dir (and world).

## Config profile (`core/config_store`, schema `webapp/app.py::_default_config`)
| Key | Type | Default |
|---|---|---|
| loader | str | "vanilla" |
| version | str | "1.21.7" |
| ram_mb | int | 2048 |
| server_port | int | 25565 |
| motd | str | "EZ Minecraft Server" |
| max_players | int | 10 |
| gamemode | str | "survival" |
| difficulty | str | "normal" |
| level_type | str | "normal" |
| view_distance | int | 10 |
| simulation_distance | int | 10 |
| level_seed | str | "" |
| online_mode | bool | True |
| pvp | bool | True |
| spawn_monsters | bool | True |
| allow_flight | bool | False |
| allow_nether | bool | True |
| white_list | bool | False |
| enable_command_block | bool | True |

Always-on keys (in `_default_config`, edited on Server setup → Always-on): `autostart_server`
(False), `autostart_playit` (False), `auto_restart` (True), `remote_dashboard` (False; Remote
access page, `POST /api/remote-dashboard`). A stale `dashboard_tunnel` key from the removed PlayIt
dashboard tunnel may remain in old configs; it is ignored.

`.env` (repo root, git-ignored, mode 600, written by `termux-setup.sh`): `EZ_WEBAPP_USERNAME`,
`EZ_WEBAPP_PASSWORD`, `EZ_WEBAPP_PORT`, shell-quoted; loaded by `run.sh`.

`_effective_config()` = defaults ∪ stored dict. `PUT /api/config` merges payload and saves.
The dashboard's `CONFIG_FIELDS` table (`dashboard.js`) maps inputs to exactly these keys.
No schema validation: any payload key is persisted; unknown keys are ignored by
`_server_properties_from_config`. `config_store.save` silently drops `OSError`.

## `server.properties`
Produced by `_server_properties_from_config` and written by `update_properties`:
server-port, online-mode, max-players, motd, level-seed, gamemode, difficulty, level-type, pvp,
view-distance, simulation-distance, spawn-monsters, allow-nether, allow-flight, white-list,
enable-command-block.
- snake_case → kebab-case; booleans → `"true"`/`"false"`; values stringified.
- Read-merge-rewrite; comments dropped.

## `eula.txt`
`accept_eula(server_dir)` writes `eula=true\n`.

## Server directory (`core/paths.py::server_dir(loader, version)`)
- Installed check (`_server_is_installed`):
  - vanilla/paper/fabric → `server.jar`
  - forge/neoforge → `{loader}-*.jar` / `{loader}*.jar` / platform args file
    (`downloaders._find_args_file`: `win_args.txt` on Windows, `unix_args.txt` elsewhere)
- `downloaders._already_installed` (used inside `prepare`) uses a narrower rule: `server.jar`, or
  `win_args.txt`, or non-installer `forge-*.jar` (no `neoforge-*.jar` check).
- Forge/NeoForge leave `installer.jar` in the server dir.

## Backups
- `POST /api/server/backup` zips `world/` → `world-backup-YYYYmmdd-HHMMSS.zip` in server dir.
- Restore renames existing `world/` → `world-backup-pre-restore-<ts>/`, then extracts zip.

## Mods
- `core/paths.py::content_folder`: `mods/` for fabric/forge/neoforge, `plugins/` for paper,
  none for vanilla (upload/install → 400). Listing still scans both `mods/` and `plugins/`.
- List = `.jar` entries; upload streams 64 KiB chunks; Modrinth install writes into the same folder.

## `RuntimeState` (in-memory)
`snapshot()` exposes state, loader, version, players (count), player_names (sorted), lan_address,
public_address, operation,
install_status, install_progress, install_message, playit.{state,address}. Not persisted.
Players: the server process's `on_output` calls `runtime.track_player_event(line)`, which matches
`]: <name> joined|left the game` (regex `state._PLAYER_EVENT`; the name must directly follow `]: `
so chat lines cannot spoof it). The set is cleared on start and on exit. Console history lives in
`WebSocketManager.history` (the unused `RuntimeState.console_history` was removed).

`state` values set by `app.py`: `stopped`, `ready` (briefly, create), `installing`, `starting`,
`running`, `stopping`, `crashed` (crash-restart limit reached). Also `stop_requested` (bool) and
`crash_times` (monotonic timestamps) drive crash detection (workflows.md §5a). `operation`: `idle`, `creating`, `installing`, `starting`, `stopping`.
`install_status`: `ready`, `installing`, `failed`.