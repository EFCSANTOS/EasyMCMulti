# Data Model

No relational DB, no migrations. State is files on disk + in-memory `RuntimeState`.

## Config profile (`core/config_store.py`, schema in `webapp/app.py::_default_config`)
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

`_effective_config()` merges defaults with stored dict.

## `server.properties` (managed by `core/server_process.py::update_properties`)
- Keys: snake_case → kebab-case (`server_port` → `server-port`, `max_players` → `max-players`).
- Booleans → `"true"`/`"false"`. Values stringified.
- Existing file is read, merged, rewritten (comments/unknown lines preserved as `key=value` only).
- Mapping produced by `_server_properties_from_config`: server-port, online-mode, max-players, motd,
  level-seed, gamemode, difficulty, level-type, pvp, view-distance, simulation-distance,
  spawn-monsters, allow-nether, allow-flight, white-list, enable-command-block.

## `eula.txt`
- Written as `eula=true\n` by `accept_eula(server_dir)`.

## Server layout
- Directory resolved by `core/paths.py::server_dir(loader, version)` (not inspected).
- Installed check (`_server_is_installed`):
  - vanilla/paper/fabric → `server.jar`
  - forge/neoforge → `{loader}-*.jar`, `{loader}*.jar`, or `libraries/**/win_args.txt`

## RuntimeState (in-memory, `webapp/state.py`)
Not persisted. `snapshot()` exposes: state, loader, version, players, lan_address, public_address,
operation, install_status, install_progress, install_message, playit.{state,address}.