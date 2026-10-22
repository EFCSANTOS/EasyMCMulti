"""World backups: naming, listing and retention (FEAT-01).

Archives live next to the world in the server folder:
    world-backup-<folder>-<YYYYmmdd-HHMMSS>[-<label>].zip
Labels: "manual" (the Backup button; never pruned unless the owner opts in), "auto" (schedule) and
"auto-pre-<reason>" (safety copies before risky operations). Archives from before labels existed
("world-backup-<folder>-<ts>.zip", or without a folder) count as manual.
"""
import datetime
import os
import re
import time

from . import worlds

PREFIX = "world-backup-"
_NAME = re.compile(r"^world-backup-(?:(?P<folder>.+?)-)?(?P<ts>\d{8}-\d{6})(?:-(?P<label>[a-z][a-z0-9-]*))?\.zip$")
_TS_FORMAT = "%Y%m%d-%H%M%S"


def parse(name):
    """{folder, created (datetime), label} for a backup file name, or None."""
    match = _NAME.match(name)
    if not match:
        return None
    try:
        created = datetime.datetime.strptime(match.group("ts"), _TS_FORMAT)
    except ValueError:
        return None
    return {"folder": match.group("folder") or "", "created": created, "label": match.group("label") or "manual"}


def is_auto(label):
    return label == "auto" or label.startswith("auto-")


def list_backups(sdir):
    """Every backup archive in sdir, newest first: {name, path, folder, created, label, size}."""
    items = []
    if not os.path.isdir(sdir):
        return items
    for name in os.listdir(sdir):
        if not (name.startswith(PREFIX) and name.endswith(".zip")):
            continue
        path = os.path.join(sdir, name)
        info = parse(name) or {"folder": "", "label": "manual",
                               "created": datetime.datetime.fromtimestamp(os.path.getmtime(path))}
        items.append({"name": name, "path": path, "size": os.path.getsize(path), **info})
    items.sort(key=lambda item: item["created"], reverse=True)
    return items


def create(sdir, folder, label):
    """Zip the world `folder` (and its dimension folders) as a new backup; returns its path."""
    stamp = time.strftime(_TS_FORMAT)
    archive = os.path.join(sdir, f"{PREFIX}{folder}-{stamp}-{label}.zip")
    if os.path.exists(archive):  # two backups in the same second
        archive = os.path.join(sdir, f"{PREFIX}{folder}-{stamp}-{label}-2.zip")
    worlds.export_zip(sdir, folder, archive)
    return archive


def to_prune(items, keep_recent, keep_daily, prune_manual=False, now=None):
    """Backups to delete. Per world folder: keep the newest `keep_recent`, plus the newest one of each of
    the last `keep_daily` days. Manual backups are kept unless `prune_manual`."""
    now = now or datetime.datetime.now()
    doomed = []
    by_folder = {}
    for item in items:
        if prune_manual or is_auto(item["label"]):
            by_folder.setdefault(item["folder"], []).append(item)
    for group in by_folder.values():
        group = sorted(group, key=lambda item: item["created"], reverse=True)
        keep = {item["name"] for item in group[:max(0, keep_recent)]}
        seen_days = set()
        for item in group:
            day = item["created"].date()
            if (now.date() - day).days < keep_daily and day not in seen_days:
                seen_days.add(day)
                keep.add(item["name"])
        doomed.extend(item for item in group if item["name"] not in keep)
    return doomed


def prune(sdir, keep_recent, keep_daily, prune_manual=False):
    """Delete what to_prune() picks; returns the deleted names."""
    deleted = []
    for item in to_prune(list_backups(sdir), keep_recent, keep_daily, prune_manual):
        try:
            os.remove(item["path"])
            deleted.append(item["name"])
        except OSError:
            pass
    return deleted
