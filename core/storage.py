"""What uses space in EZ_Server_Data, and safe cleanup (FEAT-15).

`usage()` walks the data folder (cached for USAGE_TTL_SEC: a big world has many files) and returns
items with an id, kind, size and whether they can be deleted. `delete(item_id, …)` recomputes the
list and only deletes an item it finds there as deletable, so a client can only name items, never
paths. The active server folder is never deletable.
"""
import os
import re
import shutil
import time

from . import java_manager, paths

USAGE_TTL_SEC = 300
OLD_LOG_DAYS = 30
LOW_SPACE_BYTES = 2 * 1024 ** 3
LOW_SPACE_RATIO = 0.10
_KNOWN_LOADERS = ("vanilla", "paper", "fabric", "forge", "neoforge")
_PRE_RESTORE = re.compile(r"-pre-restore-(\d{8})-\d{6}$")
_cache = {"key": None, "at": 0.0, "value": None}


def dir_size(path):
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        total += dir_size(entry.path)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _version_of(server_name):
    loader, _, version = server_name.partition("-")
    return version if loader in _KNOWN_LOADERS and version else ""


def _last_used(sdir):
    for name in ("logs/latest.log", "server.properties"):
        path = os.path.join(sdir, name)
        if os.path.exists(path):
            return os.path.getmtime(path)
    return os.path.getmtime(sdir)


def disk(path=None):
    """{total, free, low} for the drive holding the data folder."""
    usage = shutil.disk_usage(path or paths.DATA_DIR)
    low = usage.free < LOW_SPACE_BYTES or usage.free < usage.total * LOW_SPACE_RATIO
    return {"total": usage.total, "free": usage.free, "low": low}


def _scan(active_dir, now):
    items = []
    servers = paths.SERVERS_DIR
    needed_java = set()
    if os.path.isdir(servers):
        for name in sorted(os.listdir(servers)):
            sdir = os.path.join(servers, name)
            if not os.path.isdir(sdir):
                continue
            version = _version_of(name)
            if version:
                try:
                    needed_java.add(java_manager.required_java(version))
                except Exception:
                    pass
            active = os.path.normcase(os.path.abspath(sdir)) == os.path.normcase(os.path.abspath(active_dir))
            backups_size = 0
            listed_size = 0  # pre-restore folders and the installer, listed as their own items
            with os.scandir(sdir) as entries:
                for entry in entries:
                    if entry.is_file() and entry.name.startswith("world-backup-") and entry.name.endswith(".zip"):
                        backups_size += entry.stat().st_size
                    elif entry.is_dir() and _PRE_RESTORE.search(entry.name):
                        size = dir_size(entry.path)
                        listed_size += size
                        stamp = _PRE_RESTORE.search(entry.name).group(1)
                        items.append({"id": f"prerestore:{name}/{entry.name}", "kind": "pre-restore",
                                      "name": entry.name,
                                      "detail": f"World copy kept by a restore ({stamp[:4]}-{stamp[4:6]}-{stamp[6:]}) in {name}",
                                      "size": size, "deletable": True})
                    elif entry.is_file() and entry.name == "installer.jar":
                        size = entry.stat().st_size
                        listed_size += size
                        items.append({"id": f"installer:{name}", "kind": "installer", "name": f"{name}/installer.jar",
                                      "detail": "Loader installer, not needed once installed", "size": size,
                                      "deletable": True})
            special = backups_size + listed_size
            old_logs = []
            logs_dir = os.path.join(sdir, "logs")
            if os.path.isdir(logs_dir):
                for log in os.listdir(logs_dir):
                    path = os.path.join(logs_dir, log)
                    if log.endswith(".log.gz") and now - os.path.getmtime(path) > OLD_LOG_DAYS * 86400:
                        old_logs.append(os.path.getsize(path))
            if old_logs:
                items.append({"id": f"oldlogs:{name}", "kind": "logs", "name": f"{name}/logs",
                              "detail": f"{len(old_logs)} log archive(s) older than {OLD_LOG_DAYS} days",
                              "size": sum(old_logs), "deletable": True})
            if backups_size:
                items.append({"id": f"backups:{name}", "kind": "backups", "name": f"{name} backups",
                              "detail": "Managed on the Backups page", "size": backups_size, "deletable": False})
            used = time.strftime("%Y-%m-%d", time.localtime(_last_used(sdir)))
            items.append({"id": f"server:{name}", "kind": "server", "name": name, "active": active,
                          "detail": "In use: the selected loader and version" if active else f"Not in use; last used {used}",
                          "size": dir_size(sdir) - special - sum(old_logs), "deletable": not active})
    if os.path.isdir(paths.JAVA_DIR):
        for name in sorted(os.listdir(paths.JAVA_DIR)):
            match = re.match(r"^jre-(\d+)$", name)
            path = os.path.join(paths.JAVA_DIR, name)
            if not match or not os.path.isdir(path):
                continue
            unused = int(match.group(1)) not in needed_java
            items.append({"id": f"java:{name}", "kind": "java", "name": f"Java {match.group(1)}",
                          "detail": "Not needed by any server folder" if unused else "Used by a server folder",
                          "size": dir_size(path), "deletable": unused})
    other = 0
    if os.path.isdir(paths.DATA_DIR):
        for entry in os.scandir(paths.DATA_DIR):
            if entry.is_file():
                other += entry.stat().st_size
    if other:
        items.append({"id": "other", "kind": "other", "name": "Tools and settings",
                      "detail": "PlayIt/Cloudflare programs, settings, activity log", "size": other, "deletable": False})
    return items


def usage(active_dir, now=None, fresh=False):
    """{disk, items, total} for the data folder; cached for USAGE_TTL_SEC unless `fresh`."""
    now = now or time.time()
    key = os.path.abspath(active_dir)
    if not fresh and _cache["key"] == key and now - _cache["at"] < USAGE_TTL_SEC:
        return _cache["value"]
    items = sorted(_scan(active_dir, now), key=lambda item: item["size"], reverse=True)
    value = {"disk": disk(), "items": items, "total": sum(item["size"] for item in items)}
    _cache.update(key=key, at=now, value=value)
    return value


def delete(item_id, active_dir, now=None):
    """Delete one deletable item by id. Returns (name, bytes freed). Raises KeyError / PermissionError."""
    now = now or time.time()
    item = next((i for i in _scan(active_dir, now) if i["id"] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    if not item["deletable"]:
        raise PermissionError(f"{item['name']} can't be deleted here.")
    kind, _, ref = item_id.partition(":")
    if kind == "server":
        shutil.rmtree(os.path.join(paths.SERVERS_DIR, ref))
    elif kind == "prerestore":
        server, folder = ref.split("/", 1)
        shutil.rmtree(os.path.join(paths.SERVERS_DIR, server, folder))
    elif kind == "installer":
        os.remove(os.path.join(paths.SERVERS_DIR, ref, "installer.jar"))
    elif kind == "oldlogs":
        logs_dir = os.path.join(paths.SERVERS_DIR, ref, "logs")
        for log in os.listdir(logs_dir):
            path = os.path.join(logs_dir, log)
            if log.endswith(".log.gz") and now - os.path.getmtime(path) > OLD_LOG_DAYS * 86400:
                os.remove(path)
    elif kind == "java":
        shutil.rmtree(os.path.join(paths.JAVA_DIR, ref))
    _cache["key"] = None
    return item["name"], item["size"]
