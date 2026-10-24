"""Carry player progress from one world to another: inventories, stats, advancements, mod data.

For a fresh world that keeps what players earned. Everything per player lives in the source world
folder under <uuid> names: playerdata/<uuid>.dat (inventory, ender chest, XP, health, effects and
the inventories mods attach to the player, such as a worn Traveler's Backpack, Curios or Trinkets
slots), stats/<uuid>.json and advancements/<uuid>.json. Some Forge mods keep extra
playerdata/<uuid>.<ext> files next to it; they travel too.

The target must already be generated (its level.dat gives the spawn point) and the server stopped.
By default players are moved to the new world's spawn and their bed / respawn point is cleared,
since the old coordinates mean nothing in the new terrain. Mods that keep player items in world
data (e.g. data/sophisticatedbackpacks.dat) are offered by mod_data() and copied only on request.
The source world is only read.
"""
import os
import re
import shutil

from . import fsutil, nbt, players, worlds

PARTS = ("playerdata", "stats", "advancements")
_PART_EXT = {"playerdata": ".dat", "stats": ".json", "advancements": ".json"}
UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Player tags tied to where the player was in the old world; dropped when moving them to spawn.
_LOCATION_TAGS = (
    "Motion", "RootVehicle", "FallDistance", "fall_distance", "enteredNetherPosition", "ender_pearls",
    "current_explosion_impact_pos",
    "SpawnX", "SpawnY", "SpawnZ", "SpawnForced", "SpawnAngle", "SpawnDimension", "respawn",  # bed / anchor
    "SpawnWorld", "WorldUUIDLeast", "WorldUUIDMost",  # Bukkit/Paper: which world folder the player is in
)
# World folder entries that are terrain or vanilla bookkeeping, never offered as mod data.
_VANILLA_ENTRIES = {"region", "entities", "poi", "data", "playerdata", "stats", "advancements", "players",
                    "dimensions", "datapacks", "DIM-1", "DIM1", "session.lock", "uid.dat", "icon.png",
                    "paper-world.yml"}
_VANILLA_DATA = re.compile(
    r"^(raids(_end|_nether)?|idcounts|chunks|random_sequences|villages(_end|_nether)?|map_\d+"
    r"|Fortress|Mineshaft|Monument|EndCity|Temple|Village|Mansion|Stronghold|structure_.*|minecraft)(\.dat)?$")


def _world_dir(sdir, folder):
    if not worlds.valid_folder(folder):
        raise ValueError("Invalid world.")
    path = os.path.join(sdir, folder)
    if not os.path.isfile(os.path.join(path, "level.dat")):
        raise ValueError(f"The world '{folder}' has not been generated yet: switch to it and start the server once.")
    return path


def _known_names(sdir):
    """uuid -> player name from the whitelist, operators and the server's usercache.json."""
    names = {}
    entries = players.read_list(sdir, "whitelist") + players.read_list(sdir, "ops")
    cache = fsutil.read_json(os.path.join(sdir, "usercache.json"), [])
    entries += [e for e in cache if isinstance(e, dict)] if isinstance(cache, list) else []
    for entry in entries:
        if entry.get("uuid") and entry.get("name"):
            names[str(entry["uuid"]).lower()] = str(entry["name"])
    return names


def list_players(sdir, folder):
    """Players that have data in a world: [{uuid, name, parts, last_seen}], named ones first."""
    wdir = _world_dir(sdir, folder)
    found = {}
    for part, ext in _PART_EXT.items():
        part_dir = os.path.join(wdir, part)
        if not os.path.isdir(part_dir):
            continue
        for name in os.listdir(part_dir):
            uuid = name[: -len(ext)]
            if name.endswith(ext) and UUID_PATTERN.match(uuid):
                found.setdefault(uuid, set()).add(part)
    names = _known_names(sdir)
    result = []
    for uuid, parts in found.items():
        player_file = os.path.join(wdir, "playerdata", uuid + ".dat")
        result.append({"uuid": uuid, "name": names.get(uuid.lower(), ""), "parts": [p for p in PARTS if p in parts],
                       "last_seen": os.path.getmtime(player_file) if os.path.isfile(player_file) else None})
    return sorted(result, key=lambda p: (not p["name"], p["name"].lower(), p["uuid"]))


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(root, f)) for root, _dirs, files in os.walk(path) for f in files)


def mod_data(sdir, folder):
    """Non-vanilla files and folders in a world that mods keep: [{path, size}] (path relative to the world)."""
    wdir = _world_dir(sdir, folder)
    candidates = []
    for name in sorted(os.listdir(wdir)):
        if (name in _VANILLA_ENTRIES or name.startswith("level.dat") or re.match(r"^DIM-?\d+$", name)
                or name.endswith(".tmp")):
            continue
        candidates.append(name)
    data_dir = os.path.join(wdir, "data")
    if os.path.isdir(data_dir):
        for name in sorted(os.listdir(data_dir)):
            if not (_VANILLA_DATA.match(name) or name.endswith((".dat_old", ".tmp"))):
                candidates.append(f"data/{name}")
    return [{"path": path, "size": _size(os.path.join(wdir, *path.split("/")))} for path in candidates]


def _spawn(wdir):
    """(x, y, z, dimension) of a world's spawn from its level.dat."""
    _name, root = nbt.load(os.path.join(wdir, "level.dat"))
    data = root.get("Data", (None, {}))[1]
    spawn = data.get("spawn")  # 1.21.9+: spawn: {pos: [I; x, y, z], dimension: "..."}
    if spawn and spawn[0] == nbt.COMPOUND and spawn[1].get("pos", (None, None))[0] == nbt.INT_ARRAY:
        x, y, z = spawn[1]["pos"][1][:3]
        dimension = spawn[1].get("dimension", (nbt.STRING, "minecraft:overworld"))[1]
        return x, y, z, dimension
    if all(data.get(key, (None,))[0] == nbt.INT for key in ("SpawnX", "SpawnY", "SpawnZ")):
        return data["SpawnX"][1], data["SpawnY"][1], data["SpawnZ"][1], "minecraft:overworld"
    raise ValueError("Could not read the spawn point of the new world; copy without moving players to spawn.")


def _move_to_spawn(root, spawn):
    x, y, z, dimension = spawn
    for key in _LOCATION_TAGS:
        root.pop(key, None)
    root["Pos"] = (nbt.LIST, (nbt.DOUBLE, [x + 0.5, float(y), z + 0.5]))
    if root.get("Dimension", (None,))[0] == nbt.INT:
        root["Dimension"] = (nbt.INT, 0)  # before 1.16 the dimension was a number; 0 is the Overworld
    else:
        root["Dimension"] = (nbt.STRING, dimension)


def prepare(sdir, source, target, uuids=None, parts=PARTS, to_spawn=True, extras=()):
    """Check a transfer and read everything it writes (all players when uuids is None); changes nothing.

    A broken player file or a bad choice fails here, before anything in the target changes (and
    before the caller backs it up). apply() does the writing.
    """
    src, dst = _world_dir(sdir, source), _world_dir(sdir, target)
    if source == target:
        raise ValueError("Choose two different worlds.")
    requested = set(parts)
    if requested - set(PARTS):
        raise ValueError("Unknown kind of player data.")
    parts = [p for p in PARTS if p in requested]
    available = {p["uuid"]: p for p in list_players(sdir, source)}
    chosen = list(available) if uuids is None else list(dict.fromkeys(uuids))
    unknown = [u for u in chosen if u not in available]
    if unknown:
        raise ValueError(f"No data for player {unknown[0]} in that world.")
    allowed = {entry["path"] for entry in mod_data(sdir, source)}
    extras = list(dict.fromkeys(extras))
    if any(path not in allowed for path in extras):
        raise ValueError("Unknown mod data.")
    if not (chosen and parts) and not extras:
        raise ValueError("Nothing to copy: choose players and what to bring, or some mod data.")
    spawn = _spawn(dst) if to_spawn and "playerdata" in parts else None

    writes = []  # (source path, target path, NBT to write instead of copying or None)
    for uuid in chosen:
        if "playerdata" in parts:
            player_dir = os.path.join(src, "playerdata")
            main = os.path.join(player_dir, uuid + ".dat")
            if os.path.isfile(main):
                tree = None
                if spawn:
                    try:
                        name, root = nbt.load(main)
                    except (OSError, EOFError, ValueError) as exc:
                        raise ValueError(f"The player file of {available[uuid]['name'] or uuid} is damaged: {exc}") from exc
                    _move_to_spawn(root, spawn)
                    tree = (name, root)
                writes.append((main, os.path.join(dst, "playerdata", uuid + ".dat"), tree))
            for name in sorted(os.listdir(player_dir)) if os.path.isdir(player_dir) else []:
                if name.startswith(uuid + ".") and name not in (uuid + ".dat", uuid + ".dat_old") and not name.endswith(".tmp"):
                    writes.append((os.path.join(player_dir, name), os.path.join(dst, "playerdata", name), None))
        for part in ("stats", "advancements"):
            path = os.path.join(src, part, uuid + ".json")
            if part in parts and os.path.isfile(path):
                writes.append((path, os.path.join(dst, part, uuid + ".json"), None))

    return {"source": source, "target": target, "src": src, "dst": dst, "writes": writes, "extras": extras,
            "players": len(chosen), "parts": parts, "to_spawn": bool(spawn)}


def apply(plan, log=print):
    """Write a prepare()d transfer. Returns {players, parts, extras, to_spawn}."""
    src, dst = plan["src"], plan["dst"]
    for source_path, target_path, tree in plan["writes"]:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if tree:
            nbt.save(target_path, tree[0], tree[1])
        else:
            shutil.copy2(source_path, target_path)
    for path in plan["extras"]:
        source_path, target_path = os.path.join(src, *path.split("/")), os.path.join(dst, *path.split("/"))
        if os.path.isdir(source_path):
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(source_path, target_path)
        log(f"copied mod data {path}")
    parts = plan["parts"]
    log(f"copied {', '.join(parts) or 'no player data'} for {plan['players']} player(s) from '{plan['source']}' "
        f"to '{plan['target']}'" + (" and moved them to spawn" if plan["to_spawn"] else ""))
    return {key: plan[key] for key in ("players", "parts", "extras", "to_spawn")}


def transfer(sdir, source, target, uuids=None, parts=PARTS, to_spawn=True, extras=(), log=print):
    """prepare() + apply(): copy player progress from source to target."""
    return apply(prepare(sdir, source, target, uuids, parts, to_spawn, extras), log)
