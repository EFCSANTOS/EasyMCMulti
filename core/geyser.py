"""Bedrock crossplay with Geyser + Floodgate (FEAT-18).

Geyser translates Bedrock Edition (phones, consoles, Windows Bedrock) to Java; Floodgate lets those
players join without a Java account. Geyser comes from Modrinth; Floodgate too on Fabric/NeoForge,
but from GeyserMC's download server on Paper (it isn't on Modrinth for Paper). Geyser writes its config on the first start; `configure()` then sets the Bedrock port and
`auth-type: floodgate` with a line-based edit (no YAML dependency), and is safe to run every start.
"""
import os
import re

from . import downloaders, fsutil, modrinth, paths

SUPPORTED_LOADERS = ("paper", "fabric", "neoforge")
PROJECTS = ("geyser", "floodgate")
DEFAULT_PORT = 19132
FLOODGATE_SPIGOT_URL = "https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/spigot"
FLOODGATE_SPIGOT_JAR = "floodgate-spigot.jar"
_CONFIG = {
    "paper": ("plugins", "Geyser-Spigot", "config.yml"),
    "fabric": ("config", "Geyser-Fabric", "config.yml"),
    "neoforge": ("config", "Geyser-NeoForge", "config.yml"),
}


def supported(loader):
    return loader in SUPPORTED_LOADERS


def config_path(sdir, loader):
    return os.path.join(sdir, *_CONFIG[loader]) if loader in _CONFIG else ""


def install(loader, game_version, sdir, log=print):
    """Install Geyser and Floodgate (and their required dependencies).

    Modrinth has Geyser for every supported loader, but Floodgate only for Fabric/NeoForge; the
    Paper (Spigot) build of Floodgate comes from GeyserMC's own download server.
    """
    dest = paths.content_folder(loader, sdir)
    os.makedirs(dest, exist_ok=True)
    installed = [modrinth.install("geyser", loader, game_version, dest, log=log)]
    if loader == "paper":
        log("Downloading Floodgate for Paper from GeyserMC...")
        downloaders.download(FLOODGATE_SPIGOT_URL, os.path.join(dest, FLOODGATE_SPIGOT_JAR), log=log)
        installed.append(FLOODGATE_SPIGOT_JAR)
    else:
        installed.append(modrinth.install("floodgate", loader, game_version, dest, log=log))
    return installed


def uninstall(loader, sdir):
    """Remove the Geyser and Floodgate jars this app installed. Returns the removed file names."""
    dest = paths.content_folder(loader, sdir)
    removed = []
    spigot_jar = os.path.join(dest, FLOODGATE_SPIGOT_JAR)
    if os.path.isfile(spigot_jar):
        os.remove(spigot_jar)
        removed.append(FLOODGATE_SPIGOT_JAR)
    for info in modrinth.installed_projects(dest).values():
        if info.get("slug") in PROJECTS:
            os.remove(os.path.join(dest, info["filename"]))
            modrinth.forget_file(dest, info["filename"])
            removed.append(info["filename"])
    return removed


def _set_in_section(lines, sections, key, value):
    """Set `key: value` inside the first top-level section named in `sections`; True if found."""
    current = None
    for index, line in enumerate(lines):
        top = re.match(r"^([A-Za-z0-9_-]+):\s*(#.*)?$", line)
        if top:
            current = top.group(1)
            continue
        match = re.match(rf"^(\s+){re.escape(key)}:\s*([^#\n]*?)(\s*#.*)?$", line)
        if current in sections and match:
            lines[index] = f"{match.group(1)}{key}: {value}{match.group(3) or ''}"
            return True
    return False


def configure(sdir, loader, port=DEFAULT_PORT):
    """Point Geyser at `port` and Floodgate auth. False when Geyser hasn't written its config yet."""
    path = config_path(sdir, loader)
    if not path or not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()
    lines = original.split("\n")
    _set_in_section(lines, ("bedrock",), "port", str(int(port)))
    # Older configs call the Java side "remote:", newer ones "java:".
    _set_in_section(lines, ("remote", "java"), "auth-type", "floodgate")
    updated = "\n".join(lines)
    if updated != original:
        fsutil.write_text_atomic(path, updated)
    return True
