# Data Model

No DB, no migrations. Files + in-memory `RuntimeState`.

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

## Backups
- `POST /api/server/backup` zips `world/` → `world-backup-YYYYmmdd-HHMMSS.zip` in server dir.
- Restore renames existing `world/` → `world-backup-pre-restore-<ts>/`, then extracts zip.

## Mods
- `mods/` for forge/fabric/neoforge; `plugins/` otherwise.
- List = `.jar` entries; upload streams 64 KiB chunks; Modrinth install writes into the same folder.

## `RuntimeState` (in-memory)
`snapshot()` exposes state, loader, version, players, lan_address, public_address, operation,
install_status, install_progress, install_message, playit.{state,address}. Not persisted.