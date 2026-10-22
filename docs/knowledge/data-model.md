# Data Model

No DB, no migrations. Files + in-memory `RuntimeState`.

## Data root (`core/paths.py`)
`EZ_Server_Data/` next to the PyInstaller exe (`sys.frozen`) or at repo root; git-ignored.
```
EZ_Server_Data/
  launcher_config.json     config profile (config_store._FILE)
  playit_config.json       {"secret", "address"} (playit._CONFIG_FILE) — plaintext secret
  cloudflared-<os>-<arch>  Cloudflare tunnel binary, downloaded on Windows/Linux (cloudflared.ensure_binary)
  notify_config.json       alert settings (notifier._CONFIG_FILE) — plaintext SMTP app password and Discord
                           webhook URLs; last_link
  activity.jsonl           player joins/leaves/server stops, one JSON per line (core/activity.py, FEAT-12)
  resume_after_update.json {server, playit} written before an update restart, deleted on next start
  update-requirements.txt  temp requirements file used by updater.install_requirements
  playit-<os>-<arch>[.exe] PlayIt agent binary (playit._agent_path)
  java/jre-<feature>/      Temurin JRE per Java major (java_manager._install_dir)
  servers/<loader>-<version>/  one dir per loader+version; spaces → "_" (paths.server_dir)
```
Switching loader/version in config switches to a different server dir (and world).

**Atomic writes (FIX-03).** Every settings/metadata file — `launcher_config.json`,
`notify_config.json`, `playit_config.json`, `ez-worlds.json`, `.ez-modrinth.json`,
`server.properties` — is written by `core/fsutil.py` (`<file>.tmp` → flush + fsync → `os.replace`),
so a power cut never leaves a truncated file. The previous version stays as `<file>.bak`; readers
(`fsutil.read_json`) fall back to it when the main file is damaged, rewrite the main file from it
and log `[config] <file> was damaged; restored the previous copy.` to the console.

**World backups** (server dir): `world-backup-<folder>-<ts>[-<label>].zip` (workflows.md §11).
Config keys (FEAT-01): `backup_interval_hours` (6, 0–168, 0 = off), `backup_keep_recent` (8, 1–100),
`backup_keep_daily` (7, 0–60), `backup_prune_manual` (False).

Schedule keys (FEAT-06): `restart_daily_time` ("" or "HH:MM"), `idle_stop_minutes` (0, 0–1440),
`quiet_hours_start` / `quiet_hours_end` ("" or "HH:MM").

**Mod pack cache** (FEAT-04, server dir): `ez-modpack.mrpack` (the last built pack) and
`ez-modpack.json` (summary + `fingerprint`); safe to delete, rebuilt on the next export.

**Server player lists** (written by Minecraft, edited by `core/players.py` while the server is
stopped): `whitelist.json` `[{uuid, name}]`, `ops.json` `[{uuid, name, level, bypassesPlayerLimit}]`,
`banned-players.json` `[{uuid, name, created, source, expires, reason}]`, `banned-ips.json`
`[{ip, created, source, expires, reason}]` — in each server dir (workflows.md §21).

## Config profile (`core/config_store`, schema `webapp/common.py::_default_config`)
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

`level_name` (default `world`): folder of the active world (Worlds page) → `level-name`.

Always-on keys (in `_default_config`, edited on Server setup → Always-on): `autostart_server`
(False), `autostart_playit` (False), `auto_restart` (True), `remote_dashboard` (**True**; Remote
access page, `POST /api/remote-dashboard`), `auto_update_check` (True), `auto_update_install`
(False; both on Server setup → Updates, saved on toggle). Status page keys (FEAT-03, edited through
`POST /api/status-page`): `status_page_enabled` (False), `status_page_token` ("" until enabled;
server-generated only), `status_page_show_names` (True), `status_page_show_lan` (False). A stale `dashboard_tunnel` key from the removed PlayIt
dashboard tunnel may remain in old configs; it is ignored.

`.env` (repo root, git-ignored, mode 600, written by `termux-setup.sh`): `EZ_WEBAPP_USERNAME`,
`EZ_WEBAPP_PASSWORD`, `EZ_WEBAPP_PORT`, shell-quoted; loaded by `run.sh`.

`_effective_config()` = defaults ∪ stored dict, where stored values that fail validation (old or
hand-edited files) are replaced by the default and logged once as
`[config] Ignoring invalid saved value <key>=…` (`validation.clean_stored_config`); unknown stored
keys pass through. `PUT /api/config` (and the create/start payloads) go through
`validation.validate_config_update`: Pydantic `ConfigUpdate`, every `_default_config` key optional,
unknown keys refused. Ranges: `loader` one of the five; `version` `^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$`
(it is part of the server folder name); `ram_mb` 512–host RAM; `server_port` 1024–65535;
`max_players` 1–100; `view_distance`/`simulation_distance` 2–32; `gamemode`/`difficulty`/
`level_type` as listed in the form; `motd` ≤ 59 chars, one line; `level_seed` ≤ 64, one line;
`level_name` a world folder name. Errors → 422 in FastAPI's format
(`detail=[{loc:["body",<key>], msg:"<key>: …"}]`); nothing is saved. The dashboard's
`CONFIG_FIELDS` table (`static/js/server.js`) maps inputs to exactly these keys; `markInvalidFields`
outlines rejected inputs. New config keys must be added to `_default_config` **and** `ConfigUpdate`. `config_store.save` raises `OSError` (disk full, …);
`PUT /api/config` turns it into 500 "Could not save settings (disk full?)".

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

## Worlds
- Each world is a folder in the server dir (+ Paper `_nether`/`_the_end`); `ez-worlds.json` in the
  server dir maps folder → {name, created, source: upload|new|server, seed}.
- Mods/plugins folders hold `.ez-modrinth.json` (project id → {slug, title, filename, version}).

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

`state` values set by the routers: `stopped`, `ready` (briefly, create), `installing`, `starting`,
`running`, `stopping`, `crashed` (crash-restart limit reached). Also `stop_requested` (bool) and
`crash_times` (monotonic timestamps) drive crash detection (workflows.md §5a). `operation`: `idle`, `creating`, `installing`, `starting`, `stopping`.
`install_status`: `ready`, `installing`, `failed`.