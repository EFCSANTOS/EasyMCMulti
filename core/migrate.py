"""Move worlds, player lists and mods to another Minecraft version or loader (FEAT-09).

The old server folder is only read, never changed: going back is switching the version back.
`plan()` says what would happen (downgrade, loader change, mods without a build for the target,
space); `apply()` copies worlds + metadata + player lists + icon and installs the compatible mods.
"""
import hashlib
import os
import shutil
import time

import requests

from . import downloaders, modrinth, paths, storage, worlds

COPIED_FILES = (worlds.META_FILE, "whitelist.json", "ops.json", "banned-players.json", "banned-ips.json",
                "server-icon.png")
MODDED = ("fabric", "forge", "neoforge")


def _sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _world_folders(sdir):
    """Every world folder (with its Paper dimension folders) that exists in sdir."""
    folders = []
    for world in worlds.list_worlds(sdir, ""):
        if world.get("exists"):
            folders.extend(f for f in worlds.world_folders(sdir, world["folder"]) if os.path.isdir(os.path.join(sdir, f)))
    return sorted(set(folders))


def _mod_check(old_dir, loader, new_version):
    """{compatible: [{file, title, new_file, url, hashes, version}], missing: [{file, title}]} via one request."""
    jars = sorted(n for n in os.listdir(old_dir) if n.lower().endswith(".jar")) if os.path.isdir(old_dir) else []
    if not jars:
        return {"compatible": [], "missing": []}
    hashes = {_sha1(os.path.join(old_dir, jar)): jar for jar in jars}
    response = requests.post(f"{modrinth.API}/version_files/update", headers=modrinth.HEADERS, timeout=30, json={
        "hashes": sorted(hashes), "algorithm": "sha1", "loaders": modrinth._LOADER_MAP.get(loader, []),
        "game_versions": [new_version]})
    response.raise_for_status()
    found = response.json()
    titles = {info.get("filename"): info.get("title") for info in modrinth.load_record(old_dir).values()}
    compatible, missing = [], []
    for sha1, jar in hashes.items():
        version = found.get(sha1)
        files = (version or {}).get("files") or []
        primary = next((f for f in files if f.get("primary")), files[0] if files else None)
        title = titles.get(jar) or jar[:-4]
        if version and primary:
            compatible.append({"file": jar, "title": title, "new_file": primary["filename"], "url": primary["url"],
                               "hashes": primary.get("hashes", {}), "version": version.get("version_number", ""),
                               "project_id": version.get("project_id", "")})
        else:
            missing.append({"file": jar, "title": title})
    return {"compatible": sorted(compatible, key=lambda m: m["title"].lower()),
            "missing": sorted(missing, key=lambda m: m["title"].lower())}


def plan(old_sdir, old_loader, old_version, new_loader, new_version, new_sdir):
    """What a move would do. Raises requests.RequestException when Modrinth can't be reached."""
    folders = _world_folders(old_sdir)
    size = sum(storage.dir_size(os.path.join(old_sdir, f)) for f in folders)
    size += sum(os.path.getsize(os.path.join(old_sdir, n)) for n in COPIED_FILES
                if os.path.isfile(os.path.join(old_sdir, n)))
    loader_change = old_loader != new_loader
    mods = {"compatible": [], "missing": [], "carried": []}
    old_content = paths.content_folder(old_loader, old_sdir)
    if not loader_change and old_content:
        if new_loader in MODDED:
            mods.update(_mod_check(old_content, new_loader, new_version))
        else:  # Paper plugins usually work across versions: copy them as they are
            mods["carried"] = sorted(n for n in os.listdir(old_content) if n.lower().endswith(".jar")) \
                if os.path.isdir(old_content) else []
    existing = [f for f in folders if os.path.exists(os.path.join(new_sdir, f))]
    free = storage.disk(old_sdir)["free"]
    return {
        "from": {"loader": old_loader, "version": old_version}, "to": {"loader": new_loader, "version": new_version},
        "downgrade": downloaders._version_key(new_version) < downloaders._version_key(old_version),
        "loader_change": loader_change,
        "worlds": folders,
        "files": [n for n in COPIED_FILES if os.path.isfile(os.path.join(old_sdir, n))],
        "mods": mods,
        "replaces": existing,
        "size": size,
        "free": free,
        "enough_space": free > size * 1.1 + 200 * 2**20,
    }


def apply(the_plan, old_sdir, new_sdir, log=print):
    """Copy everything the plan lists into new_sdir. Folders already there are kept aside, not overwritten."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for folder in the_plan["worlds"]:
        target = os.path.join(new_sdir, folder)
        if os.path.exists(target):
            os.rename(target, f"{target}-pre-migrate-{stamp}")
        log(f"Copying {folder}...")
        shutil.copytree(os.path.join(old_sdir, folder), target)
    for name in the_plan["files"]:
        shutil.copy2(os.path.join(old_sdir, name), os.path.join(new_sdir, name))
    new_loader = the_plan["to"]["loader"]
    old_content = paths.content_folder(the_plan["from"]["loader"], old_sdir)
    new_content = paths.content_folder(new_loader, new_sdir)
    installed = []
    if new_content:
        os.makedirs(new_content, exist_ok=True)
        for jar in the_plan["mods"].get("carried", []):
            shutil.copy2(os.path.join(old_content, jar), os.path.join(new_content, jar))
            installed.append(jar)
        record = modrinth.load_record(new_content)
        for mod in the_plan["mods"].get("compatible", []):
            dest = os.path.join(new_content, mod["new_file"])
            downloads = downloaders.download(mod["url"], dest + ".download", log=log)
            if mod["hashes"].get("sha1") and _sha1(downloads) != mod["hashes"]["sha1"]:
                os.remove(downloads)
                raise RuntimeError(f"The download of {mod['title']} is corrupted.")
            os.replace(downloads, dest)
            if mod.get("project_id"):
                record[mod["project_id"]] = {"slug": mod["project_id"], "title": mod["title"],
                                             "filename": mod["new_file"], "version": mod["version"]}
            installed.append(mod["new_file"])
            log(f"Installed {mod['title']} {mod['version']}.")
        if record:
            modrinth._save_record(new_content, record)
    return installed
