"""Client modpack export: a Modrinth .mrpack friends import with one click (FEAT-04).

The pack lists every mod in the server's mods folder that players need too: jars Modrinth knows
(looked up by SHA-1 in one bulk request) are referenced by their CDN download, unknown jars are
bundled under overrides/mods/, and server-only mods (client_side "unsupported") are left out.
Format: https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack
"""
import datetime
import glob
import hashlib
import json
import os
import re
import zipfile

import requests

from . import fsutil, paths
from .modrinth import API, HEADERS

PACK_FILE = "ez-modpack.mrpack"
PACK_META = "ez-modpack.json"
# Loaders whose mods players need too, and their .mrpack dependency key.
DEPENDENCY_KEYS = {"fabric": "fabric-loader", "forge": "forge", "neoforge": "neoforge"}
ENV_VALUES = ("required", "optional", "unsupported")


def supported(loader):
    return loader in DEPENDENCY_KEYS


def _hashes(path):
    sha1, sha512 = hashlib.sha1(), hashlib.sha512()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            sha1.update(block)
            sha512.update(block)
    return sha1.hexdigest(), sha512.hexdigest()


def _library_versions(sdir, *parts):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(sdir, "libraries", *parts, "*")) if os.path.isdir(p))


def loader_version(loader, sdir, mc_version):
    """Installed loader version (e.g. Fabric "0.16.10", Forge "47.3.0", NeoForge "21.1.73"), or ""."""
    if loader == "fabric":
        found = _library_versions(sdir, "net", "fabricmc", "fabric-loader")
        if found:
            return found[-1]
        try:  # before the first start: the launcher jar records the loader it will download
            with zipfile.ZipFile(os.path.join(sdir, "server.jar")) as jar:
                text = jar.read("install.properties").decode("utf-8", "replace")
            match = re.search(r"^fabric-loader-version=(.+)$", text, re.MULTILINE)
            return match.group(1).strip() if match else ""
        except (OSError, KeyError, zipfile.BadZipFile):
            return ""
    if loader == "forge":
        found = _library_versions(sdir, "net", "minecraftforge", "forge")
        prefix = f"{mc_version}-"
        return found[-1][len(prefix):] if found and found[-1].startswith(prefix) else (found[-1] if found else "")
    if loader == "neoforge":
        found = _library_versions(sdir, "net", "neoforged", "neoforge")
        return found[-1] if found else ""
    return ""


def _jars(mods_dir):
    if not os.path.isdir(mods_dir):
        return []
    return sorted(name for name in os.listdir(mods_dir) if name.lower().endswith(".jar"))


def fingerprint(sdir, loader, mc_version, name):
    """Changes whenever the pack would change (mods added/removed/updated, version, name)."""
    mods_dir = paths.content_folder(loader, sdir) or ""
    parts = [loader, mc_version, name]
    for jar in _jars(mods_dir):
        stat = os.stat(os.path.join(mods_dir, jar))
        parts.append(f"{jar}:{stat.st_size}:{int(stat.st_mtime)}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _lookup(sha1s):
    """{sha1: version} from Modrinth for the given hashes (one request); {} when offline."""
    if not sha1s:
        return {}
    response = requests.post(f"{API}/version_files", json={"hashes": sha1s, "algorithm": "sha1"},
                             headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def _projects(project_ids):
    if not project_ids:
        return {}
    response = requests.get(f"{API}/projects", params={"ids": json.dumps(sorted(project_ids))},
                            headers=HEADERS, timeout=30)
    response.raise_for_status()
    return {project["id"]: project for project in response.json()}


def _env(value):
    return value if value in ENV_VALUES else "required"


def build(sdir, loader, mc_version, name, log=print):
    """Build (or reuse) the pack in sdir. Returns the summary dict saved next to it.

    Raises ValueError when the loader doesn't use client mods or there are no mods.
    """
    if not supported(loader):
        raise ValueError("Only Fabric, Forge and NeoForge servers need a mod pack; players join this one without mods.")
    mods_dir = paths.content_folder(loader, sdir)
    jars = _jars(mods_dir)
    if not jars:
        raise ValueError("There are no mods on this server yet.")
    pack_path = os.path.join(sdir, PACK_FILE)
    meta_path = os.path.join(sdir, PACK_META)
    key = fingerprint(sdir, loader, mc_version, name)
    cached = fsutil.read_json(meta_path, {})
    if cached.get("fingerprint") == key and os.path.isfile(pack_path):
        return cached

    hashes = {jar: _hashes(os.path.join(mods_dir, jar)) for jar in jars}
    warnings = []
    try:
        versions = _lookup([sha1 for sha1, _ in hashes.values()])
        projects = _projects({version["project_id"] for version in versions.values()})
    except requests.RequestException as exc:
        versions, projects = {}, {}
        warnings.append(f"Modrinth could not be reached ({exc}); every mod is bundled in the pack instead.")

    files, bundled, excluded = [], [], []
    for jar in jars:
        sha1, sha512 = hashes[jar]
        version = versions.get(sha1)
        remote = next((f for f in (version or {}).get("files", []) if f.get("hashes", {}).get("sha1") == sha1), None)
        if version and remote:
            project = projects.get(version["project_id"], {})
            client_side = project.get("client_side", "required")
            if client_side == "unsupported":
                excluded.append(project.get("title") or jar)
                continue
            files.append({
                "path": f"mods/{jar}",
                "hashes": {"sha1": sha1, "sha512": sha512},
                "env": {"client": _env(client_side), "server": _env(project.get("server_side", "required"))},
                "downloads": [remote["url"]],
                "fileSize": os.path.getsize(os.path.join(mods_dir, jar)),
            })
        else:
            bundled.append(jar)
    if bundled:
        warnings.append(f"{len(bundled)} mod(s) are not on Modrinth and are bundled in the pack: "
                        f"{', '.join(bundled)}. Only share them if their licence allows it.")

    dependencies = {"minecraft": mc_version}
    loader_ver = loader_version(loader, sdir, mc_version)
    if loader_ver:
        dependencies[DEPENDENCY_KEYS[loader]] = loader_ver
    else:
        warnings.append(f"The {loader} version could not be detected; start the server once, then export again.")
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": datetime.date.today().isoformat(),
        "name": name,
        "summary": "Mods for our server, exported by EZ Minecraft.",
        "files": files,
        "dependencies": dependencies,
    }
    tmp = pack_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as pack:
        pack.writestr("modrinth.index.json", json.dumps(index, indent=2))
        for jar in bundled:
            pack.write(os.path.join(mods_dir, jar), f"overrides/mods/{jar}")
    os.replace(tmp, pack_path)
    summary = {
        "fingerprint": key,
        "filename": f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', name).strip('-') or 'server'}-{mc_version}.mrpack",
        "size": os.path.getsize(pack_path),
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "modrinth": len(files),
        "bundled": bundled,
        "excluded": excluded,
        "loader_version": loader_ver,
        "warnings": warnings,
    }
    fsutil.write_json_atomic(meta_path, summary)
    log(f"Mod pack built: {len(files)} from Modrinth, {len(bundled)} bundled, {len(excluded)} server-only left out.")
    return summary


def pack_path(sdir):
    return os.path.join(sdir, PACK_FILE)
