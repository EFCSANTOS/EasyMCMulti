"""Save/load the launcher settings (single profile) as JSON.

Restores loader/version/RAM/port and the rest of the config on the next launch.
"""
import os

from . import fsutil, paths

_FILE = os.path.join(paths.DATA_DIR, "launcher_config.json")


def load():
    """Return the saved config dict ({} if missing; the .bak copy if the file is damaged)."""
    data = fsutil.read_json(_FILE, {})
    return data if isinstance(data, dict) else {}


def save(data):
    """Write the config dict atomically. Raises OSError (e.g. disk full) so callers can report it."""
    paths.ensure_dirs()
    fsutil.write_json_atomic(_FILE, data, ensure_ascii=False)
