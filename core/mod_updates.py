"""Mod and plugin update checker (FEAT-07).

`check()` asks Modrinth, in one request for the whole folder, for the newest version of every jar
it recognises by hash (installed from the dashboard or uploaded) for this loader + game version.
`update()` downloads the new jar, verifies its hashes, keeps the old one as `<name>.jar.old`
(Minecraft only loads `.jar`) and updates the install record; `rollback()` puts the old jars back
and `forget_old()` deletes them once the updated server has run fine for a while.
"""
import hashlib
import os

import requests

from . import modrinth
from .downloaders import download

OLD_SUFFIX = ".old"


def _sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _jars(dest_dir):
    if not os.path.isdir(dest_dir):
        return []
    return sorted(name for name in os.listdir(dest_dir) if name.lower().endswith(".jar"))


def _primary(version):
    files = version.get("files") or []
    return next((f for f in files if f.get("primary")), files[0] if files else None)


def check(dest_dir, loader, game_version):
    """Mods with a newer compatible version: [{file, title, current, latest, project_id, changelog,
    new_file, url, hashes, size, dependencies}]. One Modrinth request."""
    hashes = {_sha1(os.path.join(dest_dir, jar)): jar for jar in _jars(dest_dir)}
    if not hashes:
        return []
    response = requests.post(f"{modrinth.API}/version_files/update", headers=modrinth.HEADERS, timeout=30, json={
        "hashes": sorted(hashes), "algorithm": "sha1",
        "loaders": modrinth._LOADER_MAP.get(loader, []), "game_versions": [game_version]})
    response.raise_for_status()
    by_file = {info.get("filename"): info for info in modrinth.load_record(dest_dir).values()}
    updates = []
    for sha1, version in response.json().items():
        jar = hashes.get(sha1)
        new = _primary(version)
        if not jar or not new or new.get("hashes", {}).get("sha1") == sha1:
            continue  # unknown, or already the newest
        recorded = by_file.get(jar, {})
        updates.append({
            "file": jar,
            "title": recorded.get("title") or jar[:-4],
            "current": recorded.get("version") or jar[:-4],
            "latest": version.get("version_number", ""),
            "project_id": version.get("project_id", ""),
            "changelog": modrinth._plain_text(version.get("changelog") or "", 400),
            "new_file": new["filename"],
            "url": new["url"],
            "hashes": new.get("hashes", {}),
            "size": new.get("size", 0),
            "dependencies": [d["project_id"] for d in version.get("dependencies", [])
                             if d.get("dependency_type") == "required" and d.get("project_id")],
        })
    return sorted(updates, key=lambda item: item["title"].lower())


def update(dest_dir, item, loader, game_version, log=print):
    """Replace item["file"] with the new version. The old jar stays as <file>.old until forget_old().

    Raises (and leaves the old jar in place) when the download fails or its hash doesn't match.
    """
    new_path = os.path.join(dest_dir, item["new_file"])
    staging = new_path + ".download"
    try:
        download(item["url"], staging, log=log)
        expected = item.get("hashes", {})
        with open(staging, "rb") as f:
            data_hashes = {"sha1": hashlib.sha1(), "sha512": hashlib.sha512()}
            for block in iter(lambda: f.read(1 << 20), b""):
                for digest in data_hashes.values():
                    digest.update(block)
        for algorithm, digest in data_hashes.items():
            if expected.get(algorithm) and digest.hexdigest() != expected[algorithm]:
                raise RuntimeError(f"The download of {item['title']} is corrupted ({algorithm} mismatch).")
        old_path = os.path.join(dest_dir, item["file"])
        os.replace(old_path, old_path + OLD_SUFFIX)
        os.replace(staging, new_path)
    finally:
        if os.path.exists(staging):
            os.remove(staging)
    record = modrinth.load_record(dest_dir)
    entry = next((info for info in record.values() if info.get("filename") == item["file"]), None)
    if entry is None:  # an uploaded jar Modrinth recognised: record it from now on
        entry = record.setdefault(item["project_id"] or item["file"], {"slug": item["project_id"], "title": item["title"]})
    if entry.get("previous"):  # updated twice before the old jar was cleaned up: keep one generation
        stale = os.path.join(dest_dir, entry["previous"] + OLD_SUFFIX)
        if os.path.exists(stale):
            os.remove(stale)
    entry.update(filename=item["new_file"], version=item["latest"], previous=item["file"],
                 previous_version=item["current"])
    modrinth._save_record(dest_dir, record)
    installed = set(modrinth.installed_projects(dest_dir))
    for dependency in item.get("dependencies", []):
        if dependency not in installed:
            try:
                modrinth.install(dependency, loader, game_version, dest_dir, log=log)
            except (RuntimeError, requests.RequestException) as exc:
                log(f"Warning: could not install a dependency of {item['title']}: {exc}")
    log(f"Updated {item['title']} {item['current']} -> {item['latest']}.")
    return item["new_file"]


def old_jars(dest_dir):
    if not os.path.isdir(dest_dir):
        return []
    return sorted(name for name in os.listdir(dest_dir) if name.lower().endswith(".jar" + OLD_SUFFIX))


def rollback(dest_dir):
    """Put every <name>.jar.old back, removing the jar that replaced it. Returns the restored names."""
    record = modrinth.load_record(dest_dir)
    restored = []
    for old in old_jars(dest_dir):
        original = old[: -len(OLD_SUFFIX)]
        replacement = next((info for info in record.values() if info.get("previous") == original), None)
        if replacement and os.path.exists(os.path.join(dest_dir, replacement["filename"])):
            os.remove(os.path.join(dest_dir, replacement["filename"]))
            replacement["filename"] = original
            replacement["version"] = replacement.pop("previous_version", "")
            replacement.pop("previous", None)
        os.replace(os.path.join(dest_dir, old), os.path.join(dest_dir, original))
        restored.append(original)
    modrinth._save_record(dest_dir, record)
    return restored


def forget_old(dest_dir):
    """Delete the kept old jars (after the updated server ran fine). Returns how many."""
    record = modrinth.load_record(dest_dir)
    for info in record.values():
        info.pop("previous", None)
        info.pop("previous_version", None)
    removed = 0
    for old in old_jars(dest_dir):
        try:
            os.remove(os.path.join(dest_dir, old))
            removed += 1
        except OSError:
            pass
    if removed:
        modrinth._save_record(dest_dir, record)
    return removed
