# Data Model

No DB, no migrations. Files + in-memory `RuntimeState`.

## Data root (`core/paths.py`)
`EZ_Server_Data/` next to the PyInstaller exe (`sys.frozen`) or at repo root; git-ignored.
```
EZ_Server_Data/
  launcher_config.json     config profile (config_store._FILE)
  playit_config.json       {"secret", "address"} (playit._CONFIG_FILE) — plaintext secret
  mail_config.json         SMTP creds (mailer._CREDS_FILE; unused by web app)
  playit-<os>-<arch>[.exe] PlayIt agent binary (playit._agent_path)
  java/jre-<feature>/      Temurin JRE per Java major (java_manager._install_dir)
  servers/<loader>-<version>/  one dir per loader+version; spaces → "_" (paths.server_dir)
  crash_reports/           crash_reporter (unused by web app)
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

`_effective_config()` = defaults ∪ stored dict. `PUT /api/config` merges payload and saves.
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
  - forge/neoforge → `{loader}-*.jar` / `{loader}*.jar` / `libraries/**/win_args.txt`
- `downloaders._already_installed` (used inside `prepare`) uses a narrower rule: `server.jar`, or
  `win_args.txt`, or non-installer `forge-*.jar` (no `neoforge-*.jar` check).
- Forge/NeoForge leave `installer.jar` in the server dir.

## Backups
- `POST /api/server/backup` zips `world/` → `world-backup-YYYYmmdd-HHMMSS.zip` in server dir.
- Restore renames existing `world/` → `world-backup-pre-restore-<ts>/`, then extracts zip.

## Mods
- `mods/` for forge/fabric/neoforge; `plugins/` otherwise (incl. vanilla, which loads neither).
  `core/paths.py::content_folder` has a different (unused) mapping.
- List = `.jar` entries; upload streams 64 KiB chunks; Modrinth install writes into the same folder.

## `RuntimeState` (in-memory)
`snapshot()` exposes state, loader, version, players, lan_address, public_address, operation,
install_status, install_progress, install_message, playit.{state,address}. Not persisted.
`players` is never updated (always 0); `console_history` is unused (history lives in
`WebSocketManager.history`).

`state` values set by `app.py`: `stopped`, `ready` (briefly, create), `installing`, `starting`,
`running`, `stopping`. `operation`: `idle`, `creating`, `installing`, `starting`, `stopping`.
`install_status`: `ready`, `installing`, `failed`.