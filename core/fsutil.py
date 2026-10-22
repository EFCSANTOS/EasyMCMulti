"""Crash-safe file writes and damaged-file recovery.

A phone that dies or a PC that loses power mid-save must never leave a truncated settings file:
the app would silently fall back to defaults (a different server folder) and the world would
seem gone. Writes go to `<file>.tmp`, are flushed to disk, then renamed over the original
(atomic on Windows and POSIX). The previous version is kept as `<file>.bak`, and readers fall
back to it when the main file is damaged.
"""
import json
import os
import shutil
import time

_log = None


def set_logger(log):
    """Route recovery messages (e.g. "restored the previous copy") to `log(str)`."""
    global _log
    _log = log


def _replace(src, dst):
    # An antivirus scanning the target can briefly hold it on Windows; retry before giving up.
    for attempt in range(3):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.1)


def _write(path, text, keep_backup):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if keep_backup and os.path.isfile(path):
            shutil.copyfile(path, path + ".bak")
        _replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_text_atomic(path, text):
    """Replace `path` with `text` atomically, keeping the previous version as `path + ".bak"`.

    Raises OSError (e.g. disk full) instead of hiding it.
    """
    _write(path, text, keep_backup=True)


def write_json_atomic(path, data, ensure_ascii=True):
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=ensure_ascii))


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_json(path, default=None):
    """Parsed JSON from `path`, else from `path + ".bak"` (and repair `path`), else `default`.

    A missing file without a backup is normal (first run) and returns `default` quietly.
    """
    try:
        return _load_json(path)
    except FileNotFoundError:
        if not os.path.isfile(path + ".bak"):
            return default
    except (OSError, ValueError):
        pass
    try:
        data = _load_json(path + ".bak")
    except (OSError, ValueError):
        return default
    name = os.path.basename(path)
    try:
        with open(path + ".bak", "r", encoding="utf-8") as f:
            _write(path, f.read(), keep_backup=False)
        message = f"{name} was damaged; restored the previous copy."
    except OSError as exc:
        message = f"{name} was damaged; using the previous copy (could not repair it: {exc})."
    if _log is not None:
        _log(message)
    return data
