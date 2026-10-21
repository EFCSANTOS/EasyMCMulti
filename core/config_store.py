"""Save/load the launcher settings (single profile) as JSON.

Restores loader/version/RAM/port and the rest of the config on the next launch.
"""
import json
import os

from . import paths

_FILE = os.path.join(paths.DATA_DIR, "launcher_config.json")


def load():
    """Return the saved config dict, or {} if missing/unreadable."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data):
    """Write the config dict. Silently ignores disk errors."""
    try:
        paths.ensure_dirs()
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
