"""Emplacement des dossiers de donnees.

Tout est range dans un dossier "MZ_Server_Data" place a cote du .exe
(ou a la racine du projet quand on lance depuis les sources).
Comme ca, copier le .exe sur un autre PC suffit : il recree tout au 1er lancement.
"""
import os
import sys


def base_dir():
    """Dossier de base : a cote du .exe une fois compile, sinon racine du projet."""
    if getattr(sys, "frozen", False):  # compile par PyInstaller
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DATA_DIR = os.path.join(base_dir(), "MZ_Server_Data")
JAVA_DIR = os.path.join(DATA_DIR, "java")
SERVERS_DIR = os.path.join(DATA_DIR, "servers")


def ensure_dirs():
    for d in (DATA_DIR, JAVA_DIR, SERVERS_DIR):
        os.makedirs(d, exist_ok=True)


def server_dir(loader, version):
    """Un dossier par couple loader+version, ex: servers/paper-1.21.4 (monde persistant)."""
    name = f"{loader}-{version}".replace(" ", "_")
    d = os.path.join(SERVERS_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d


def list_servers():
    """Liste les serveurs deja installes (noms de dossiers 'loader-version'), recents d'abord."""
    if not os.path.isdir(SERVERS_DIR):
        return []
    dirs = [n for n in os.listdir(SERVERS_DIR)
            if os.path.isdir(os.path.join(SERVERS_DIR, n))]
    # plus recemment modifies en premier (dernier joue en haut)
    dirs.sort(key=lambda n: os.path.getmtime(os.path.join(SERVERS_DIR, n)), reverse=True)
    return dirs


def content_folder(loader, sdir):
    """Dossier ou deposer mods/plugins selon le loader (None si vanilla)."""
    if loader == "paper":
        return os.path.join(sdir, "plugins")
    if loader in ("fabric", "forge"):
        return os.path.join(sdir, "mods")
    return None  # vanilla : pas de mods/plugins
