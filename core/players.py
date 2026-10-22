"""Whitelist, operators and bans: the server's own JSON files in the server folder.

While the server runs, the dashboard changes these lists with console commands (the server keeps
the files in sync and resolves UUIDs itself). While it is stopped, the files are edited here, with
UUIDs from the Mojang profile API (online mode) or the offline-mode UUID the server would compute.
"""
import datetime
import hashlib
import os
import re
import uuid

import requests

from . import fsutil

FILES = {
    "whitelist": "whitelist.json",
    "ops": "ops.json",
    "bans": "banned-players.json",
    "ips": "banned-ips.json",
}
PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{name}"
DEFAULT_OP_LEVEL = 4

# "[..] [Server thread/INFO]: Steve (/1.2.3.4:5678) lost connection: You are not white-listed on this server!"
# "[..]: Disconnecting Steve (/1.2.3.4:5678): You are not white-listed on this server!"
# older: "... GameProfile@1a2b[id=...,name=Steve,...] (/1.2.3.4:5678) lost connection: You are not white-listed"
# Anchored to the "[time] [thread/LEVEL]: " prefix so chat ("]: <Steve> ...") can't fake it.
_LINE_START = r"^\[[^\]]*\] \[[^\]]*\]: "
_ADDRESS = r" \(/[^)]*\)"
_NOT_WHITELISTED = r": You are not white-?listed"
_REJECTED = [re.compile(pattern, re.IGNORECASE) for pattern in (
    _LINE_START + r"(\.?\w{1,16})" + _ADDRESS + r" lost connection" + _NOT_WHITELISTED,
    _LINE_START + r"Disconnecting (\.?\w{1,16})" + _ADDRESS + _NOT_WHITELISTED,
    _LINE_START + r"\S*GameProfile@\w+\[[^\]]*?name=(\.?\w{1,16})[,\]][^\]]*\]?" + _ADDRESS + r" lost connection"
    + _NOT_WHITELISTED,
)]


def _path(sdir, kind):
    return os.path.join(sdir, FILES[kind])


def read_list(sdir, kind):
    data = fsutil.read_json(_path(sdir, kind), [])
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def write_list(sdir, kind, entries):
    fsutil.write_json_atomic(_path(sdir, kind), entries)


def offline_uuid(name):
    """The UUID an offline-mode server gives `name`: UUID v3 of "OfflinePlayer:<name>"."""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def resolve_uuid(name, online_mode):
    """(uuid, canonical name) for `name`. Raises ValueError if the account does not exist."""
    if not online_mode:
        return offline_uuid(name), name
    if name.startswith("."):
        raise ValueError("Bedrock players can only be added while the server is running.")
    try:
        response = requests.get(PROFILE_URL.format(name=name), timeout=10)
    except requests.RequestException as exc:
        raise ValueError(f"Could not reach Mojang to look up {name}: {exc}") from exc
    if response.status_code in (204, 404):
        raise ValueError(f"There is no Minecraft account named {name}.")
    if not response.ok:
        raise ValueError(f"Mojang lookup failed (HTTP {response.status_code}).")
    data = response.json()
    return str(uuid.UUID(hex=data["id"])), data.get("name") or name


def _without(entries, name):
    return [entry for entry in entries if str(entry.get("name", "")).lower() != name.lower()]


def contains(sdir, kind, name):
    return any(str(entry.get("name", "")).lower() == name.lower() for entry in read_list(sdir, kind))


def add(sdir, kind, name, online_mode, level=DEFAULT_OP_LEVEL, reason=""):
    """Add `name` to whitelist/ops/bans (server stopped). Returns the canonical name."""
    player_uuid, canonical = resolve_uuid(name, online_mode)
    entry = {"uuid": player_uuid, "name": canonical}
    if kind == "ops":
        entry.update(level=int(level), bypassesPlayerLimit=False)
    elif kind == "bans":
        entry.update(created=datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
                     source="EZ Minecraft dashboard", expires="forever", reason=reason or "Banned by an operator.")
    write_list(sdir, kind, _without(read_list(sdir, kind), canonical) + [entry])
    return canonical


def remove(sdir, kind, name):
    """Remove `name` (or an IP for kind "ips"). Returns True if it was listed."""
    entries = read_list(sdir, kind)
    if kind == "ips":
        kept = [entry for entry in entries if entry.get("ip") != name]
    else:
        kept = _without(entries, name)
    if len(kept) == len(entries):
        return False
    write_list(sdir, kind, kept)
    return True


def rejected_name(line):
    """Name of a player refused by the whitelist in a console line, else None."""
    for pattern in _REJECTED:
        match = pattern.search(line)
        if match:
            return match.group(1)
    return None
