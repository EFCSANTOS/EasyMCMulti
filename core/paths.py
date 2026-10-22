"""Data folder locations.

Everything lives in an "EZ_Server_Data" folder next to the .exe
(or at the project root when running from source).
That way, copying the .exe to another PC is enough: it recreates everything on first launch.
"""
import os
import sys


def base_dir():
    """Base folder: next to the .exe once frozen, otherwise the project root."""
    if getattr(sys, "frozen", False):  # frozen by PyInstaller
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DATA_DIR = os.path.join(base_dir(), "EZ_Server_Data")
JAVA_DIR = os.path.join(DATA_DIR, "java")
SERVERS_DIR = os.path.join(DATA_DIR, "servers")


def ensure_dirs():
    for d in (DATA_DIR, JAVA_DIR, SERVERS_DIR):
        os.makedirs(d, exist_ok=True)


def server_dir(loader, version):
    """One folder per loader+version pair, e.g. servers/paper-1.21.4 (persistent world)."""
    name = f"{loader}-{version}".replace(" ", "_")
    d = os.path.join(SERVERS_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d


def content_folder(loader, sdir):
    """Folder where mods/plugins go for the given loader (None for vanilla)."""
    if loader == "paper":
        return os.path.join(sdir, "plugins")
    if loader in ("fabric", "forge", "neoforge"):
        return os.path.join(sdir, "mods")
    return None  # vanilla: no mods/plugins
