"""server.properties: descriptions and types for common keys, and value escaping (FEAT-14).

Keys written by the Settings form (`common._server_properties_from_config`) are "managed": the
All-properties table shows them read-only so the two editors never fight. Everything else can be
set there and is stored in config as `extra_properties`.
"""
from __future__ import annotations

import re
from typing import Any

# key: (type, description[, options]) — type is "bool", "int", "enum" or "text".
KNOWN: dict[str, tuple[Any, ...]] = {
    "allow-flight": ("bool", "Let players fly in survival (needed by some mods); otherwise flying kicks them."),
    "allow-nether": ("bool", "Whether the Nether exists."),
    "broadcast-console-to-ops": ("bool", "Show console command output to operators in game."),
    "broadcast-rcon-to-ops": ("bool", "Show RCON command output to operators in game."),
    "difficulty": ("enum", "Game difficulty.", ["peaceful", "easy", "normal", "hard"]),
    "enable-command-block": ("bool", "Allow command blocks."),
    "enable-query": ("bool", "Answer GameSpy4 queries (server list sites)."),
    "enable-rcon": ("bool", "Remote console over the network. Leave off unless you know you need it."),
    "enable-status": ("bool", "Show the server as online in the multiplayer list."),
    "enforce-secure-profile": ("bool", "Require players to have signed chat (Mojang-signed profiles)."),
    "enforce-whitelist": ("bool", "Kick players who aren't whitelisted when the whitelist is reloaded."),
    "entity-broadcast-range-percentage": ("int", "How far away entities are sent to players, in percent (10–1000)."),
    "force-gamemode": ("bool", "Put players in the default game mode every time they join."),
    "function-permission-level": ("int", "Permission level for functions (1–4)."),
    "gamemode": ("enum", "Default game mode.", ["survival", "creative", "adventure", "spectator"]),
    "generate-structures": ("bool", "Generate villages, temples and other structures in new chunks."),
    "hardcore": ("bool", "Hardcore: one life, then spectator. Can't be undone for a world."),
    "hide-online-players": ("bool", "Hide the player list in the multiplayer list."),
    "level-seed": ("text", "Seed for new worlds."),
    "level-type": ("text", "World type for new worlds (e.g. minecraft:normal, minecraft:flat)."),
    "max-chained-neighbor-updates": ("int", "Limit for chained redstone updates (-1 = no limit)."),
    "max-players": ("int", "Maximum number of players at once."),
    "max-tick-time": ("int", "Milliseconds a tick may take before the watchdog stops the server (-1 = never)."),
    "max-world-size": ("int", "World border radius in blocks."),
    "motd": ("text", "Message shown in the multiplayer list."),
    "network-compression-threshold": ("int", "Packets larger than this many bytes are compressed (-1 = never)."),
    "online-mode": ("bool", "Check accounts with Mojang. Turn off only for offline/LAN play."),
    "op-permission-level": ("int", "Default operator level (1–4)."),
    "player-idle-timeout": ("int", "Kick players idle for this many minutes (0 = never)."),
    "prevent-proxy-connections": ("bool", "Refuse players connecting through a VPN/proxy."),
    "pvp": ("bool", "Players can hurt each other."),
    "rate-limit": ("int", "Packets per second before a player is kicked (0 = no limit)."),
    "require-resource-pack": ("bool", "Kick players who decline the resource pack."),
    "resource-pack": ("text", "Download URL of a resource pack players get when they join."),
    "resource-pack-prompt": ("text", "Message shown when asking players to accept the resource pack."),
    "resource-pack-sha1": ("text", "SHA-1 of the resource pack file (lets clients cache it)."),
    "simulation-distance": ("int", "How far from players the world keeps running, in chunks."),
    "spawn-monsters": ("bool", "Hostile mobs spawn."),
    "spawn-protection": ("int", "Radius around spawn that only operators can build in (0 = off)."),
    "sync-chunk-writes": ("bool", "Write chunks synchronously (safer on power loss, slower)."),
    "use-native-transport": ("bool", "Use Linux's faster network transport when available."),
    "view-distance": ("int", "How far players can see, in chunks."),
    "white-list": ("bool", "Only whitelisted players can join."),
}

# Written by the Settings form on every start (common._server_properties_from_config).
MANAGED = {
    "server-port", "online-mode", "max-players", "motd", "level-seed", "level-name", "gamemode", "difficulty",
    "level-type", "pvp", "view-distance", "simulation-distance", "spawn-monsters", "allow-nether", "allow-flight",
    "white-list", "enable-command-block",
}
# Never through the raw table: the dashboard owns these (network, world selection, whitelist).
LOCKED = {"server-port", "server-ip", "level-name", "white-list", "rcon.password", "query.port", "rcon.port"}
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def describe(key: str) -> dict[str, Any]:
    meta = KNOWN.get(key)
    if not meta:
        return {"type": "text", "description": ""}
    return {"type": meta[0], "description": meta[1], **({"options": meta[2]} if len(meta) > 2 else {})}


def escape_value(value: str) -> str:
    """Non-ASCII characters (e.g. § colour codes) as \\uXXXX, which Java reads whatever the file encoding.

    Backslash escapes the owner typed (like \\n for a second MOTD line) are left as they are.
    """
    return "".join(char if ord(char) < 128 else f"\\u{ord(char):04x}" for char in value)
