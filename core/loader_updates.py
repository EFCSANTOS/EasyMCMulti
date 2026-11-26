"""Update the Fabric loader of an installed server without changing the Minecraft version.

A Fabric server.jar is a small launcher that records the loader it runs in `install.properties` and
downloads that loader on the first start, so an install keeps its loader forever. `update()` swaps in
a launcher for the newest stable loader built for the same game version and keeps the old one as
server.jar.previous, which `rollback()` puts back. Moving to another Minecraft version is FEAT-09
(core/migrate.py); this only changes the loader.
"""
import os
import re
import zipfile

from . import downloaders

SUPPORTED = ("fabric",)
JAR = "server.jar"
PREVIOUS = "server.jar.previous"


def supported(loader):
    return loader in SUPPORTED


def jar_loader_version(path):
    """Loader version recorded in a Fabric launcher jar, or "" when it isn't one."""
    try:
        with zipfile.ZipFile(path) as jar:
            text = jar.read("install.properties").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    match = re.search(r"^fabric-loader-version=(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def status(sdir, game_version):
    """{current, latest, update_available, previous}. Raises requests.RequestException when offline."""
    current = jar_loader_version(os.path.join(sdir, JAR))
    latest = downloaders.fabric_latest_loader(game_version)
    previous = jar_loader_version(os.path.join(sdir, PREVIOUS))
    newer = bool(current and latest and downloaders._version_key(latest) > downloaders._version_key(current))
    return {"current": current, "latest": latest, "update_available": newer, "previous": previous}


def update(sdir, game_version, log=print):
    """Install the newest stable loader for game_version. Returns (old, new) loader versions."""
    jar = os.path.join(sdir, JAR)
    old = jar_loader_version(jar)
    if not old:
        raise RuntimeError("This server folder has no Fabric server.jar to update.")
    new = downloaders.fabric_latest_loader(game_version)
    if downloaders._version_key(new) <= downloaders._version_key(old):
        raise RuntimeError(f"Fabric loader {old} is already the newest for Minecraft {game_version}.")
    log(f"Downloading Fabric loader {new} for Minecraft {game_version}...")
    part = jar + ".download"
    downloaders.download(downloaders.fabric_server_url(game_version, new), part, log=log)
    got = jar_loader_version(part)
    if got != new:
        os.remove(part)
        raise RuntimeError(f"The download is not a Fabric {new} server (got {got or 'an unknown file'}).")
    os.replace(jar, os.path.join(sdir, PREVIOUS))
    os.replace(part, jar)
    log(f"Fabric loader {old} -> {new}. The next start downloads the new loader.")
    return old, new


def rollback(sdir, log=print):
    """Swap server.jar and server.jar.previous back. Returns (from, to) loader versions."""
    jar, previous = os.path.join(sdir, JAR), os.path.join(sdir, PREVIOUS)
    back_to = jar_loader_version(previous)
    if not back_to:
        raise RuntimeError("There is no previous Fabric loader to go back to.")
    now = jar_loader_version(jar)
    if not now:  # nothing usable in server.jar: just put the previous one back
        os.replace(previous, jar)
    else:
        swap = jar + ".swap"
        os.replace(jar, swap)
        os.replace(previous, jar)
        os.replace(swap, previous)
    log(f"Fabric loader {now or '(none)'} -> {back_to} (rolled back).")
    return now, back_to
