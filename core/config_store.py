"""Sauvegarde/chargement des reglages du launcher (un seul profil) en JSON.

Permet de retrouver loader/version/RAM/port et toute la config au prochain lancement.
"""
import json
import os

from . import paths

_FILE = os.path.join(paths.DATA_DIR, "launcher_config.json")


def load():
    """Renvoie le dict de config sauvegarde, ou {} si rien/illisible."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data):
    """Ecrit le dict de config. Silencieux en cas d'erreur disque."""
    try:
        paths.ensure_dirs()
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
