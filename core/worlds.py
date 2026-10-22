"""Multiple worlds per server: list, create, import (zip), rename, delete, export.

Each world is a folder in the server directory; Minecraft loads the one named by `level-name` in
server.properties, so switching worlds only changes that setting (nothing is copied or deleted).
Paper/Spigot keep the Nether and End in sibling folders (<world>_nether, <world>_the_end); they
travel with their world. Display names live in <server dir>/ez-worlds.json (folder -> name), so a
rename never moves files.
"""
import datetime
import json
import os
import re
import shutil
import zipfile

META_FILE = "ez-worlds.json"
DIMENSION_SUFFIXES = ("_nether", "_the_end")
FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def valid_folder(folder):
    return bool(FOLDER_PATTERN.match(folder or "")) and ".." not in folder


def _meta_path(sdir):
    return os.path.join(sdir, META_FILE)


def load_meta(sdir):
    try:
        with open(_meta_path(sdir), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_meta(sdir, meta):
    with open(_meta_path(sdir), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def slugify(name):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-._").lower()
    return slug[:48] or "world"


def unique_folder(sdir, name):
    base = slugify(name)
    folder, n = base, 2
    meta = load_meta(sdir)
    while os.path.exists(os.path.join(sdir, folder)) or folder in meta:
        folder = f"{base}-{n}"
        n += 1
    return folder


def world_folders(sdir, folder):
    """The world folder plus its Paper dimension siblings that exist."""
    return [name for name in (folder, *(folder + s for s in DIMENSION_SUFFIXES))
            if os.path.isdir(os.path.join(sdir, name))]


def _folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def list_worlds(sdir, active):
    """Worlds on disk (folders with level.dat) plus created-but-not-generated ones from metadata."""
    meta = load_meta(sdir)
    found = {}
    if os.path.isdir(sdir):
        for entry in sorted(os.listdir(sdir)):
            path = os.path.join(sdir, entry)
            if not os.path.isfile(os.path.join(path, "level.dat")):
                continue
            base = next((entry[: -len(s)] for s in DIMENSION_SUFFIXES if entry.endswith(s)), None)
            if base and os.path.isdir(os.path.join(sdir, base)):
                continue  # a Paper dimension folder of another world
            found[entry] = path
    worlds = []
    for folder in sorted(set(found) | set(meta) | {active}):
        info = meta.get(folder, {})
        exists = folder in found
        size = sum(_folder_size(os.path.join(sdir, f)) for f in world_folders(sdir, folder)) if exists else 0
        level_dat = os.path.join(sdir, folder, "level.dat")
        worlds.append({
            "folder": folder,
            "name": info.get("name") or folder,
            "active": folder == active,
            "exists": exists,
            "size_bytes": size,
            "last_played": os.path.getmtime(level_dat) if exists else None,
            "created": info.get("created"),
            "source": info.get("source", "server" if exists else "new"),
            "seed": info.get("seed", ""),
        })
    return worlds


def _remember(sdir, folder, **fields):
    meta = load_meta(sdir)
    entry = meta.get(folder, {})
    entry.update(fields)
    entry.setdefault("created", datetime.datetime.now().isoformat(timespec="seconds"))
    meta[folder] = entry
    _save_meta(sdir, meta)


def create(sdir, name, seed=""):
    """Register a new world; Minecraft generates it the first time it starts on it."""
    folder = unique_folder(sdir, name)
    _remember(sdir, folder, name=name.strip() or folder, source="new", seed=str(seed or ""))
    return folder


def rename(sdir, folder, name):
    if not valid_folder(folder):
        raise ValueError("Invalid world.")
    name = name.strip()
    if not name:
        raise ValueError("A name is required.")
    _remember(sdir, folder, name=name[:80])


def delete(sdir, folder):
    """Delete a world and its dimension folders permanently."""
    if not valid_folder(folder):
        raise ValueError("Invalid world.")
    for name in world_folders(sdir, folder):
        shutil.rmtree(os.path.join(sdir, name))
    meta = load_meta(sdir)
    if meta.pop(folder, None) is not None:
        _save_meta(sdir, meta)


def _world_root(names):
    """Find the world inside a zip: the shallowest level.dat that is not a Paper dimension folder."""
    roots = []
    for name in names:
        clean = name.replace("\\", "/")
        if clean.endswith("level.dat") and "__MACOSX" not in clean:
            prefix = clean[: -len("level.dat")]
            folder = prefix.rstrip("/").split("/")[-1] if prefix else ""
            if not folder.endswith(DIMENSION_SUFFIXES):
                roots.append(prefix)
    if not roots:
        raise ValueError("No Minecraft world found in this zip (there is no level.dat).")
    return min(roots, key=lambda p: (p.count("/"), len(p)))


def _safe_extract(zf, member, prefix, target_dir):
    relative = member.replace("\\", "/")[len(prefix):]
    if not relative or relative.endswith("/"):
        return
    parts = relative.split("/")
    if relative.startswith("/") or ".." in parts or ":" in parts[0]:
        raise ValueError(f"Unsafe path in zip: {member}")
    destination = os.path.join(target_dir, *parts)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with zf.open(member) as source, open(destination, "wb") as out:
        shutil.copyfileobj(source, out, 1 << 20)


def import_zip(sdir, zip_path, name):
    """Import a zipped world (singleplayer save or server world) as a new world. Returns its folder."""
    folder = unique_folder(sdir, name)
    created = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
            prefix = _world_root(members)
            # Paper exports: <world>/, <world>_nether/, <world>_the_end/ next to each other.
            base = prefix.rstrip("/")
            targets = [(prefix, folder)]
            if base:
                for suffix in DIMENSION_SUFFIXES:
                    sibling = f"{base}{suffix}/"
                    if any(m.replace("\\", "/").startswith(sibling) for m in members):
                        targets.append((sibling, folder + suffix))
            for member_prefix, target in targets:
                target_dir = os.path.join(sdir, target)
                os.makedirs(target_dir)
                created.append(target_dir)
                for member in members:
                    if member.replace("\\", "/").startswith(member_prefix):
                        _safe_extract(zf, member, member_prefix, target_dir)
    except (zipfile.BadZipFile, ValueError, OSError):
        for path in created:
            shutil.rmtree(path, ignore_errors=True)
        raise
    _remember(sdir, folder, name=name.strip() or folder, source="upload")
    return folder


def export_zip(sdir, folder, dest_path):
    """Zip a world (with its dimension folders) to dest_path."""
    if not valid_folder(folder) or not os.path.isdir(os.path.join(sdir, folder)):
        raise ValueError("World not found.")
    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in world_folders(sdir, folder):
            root_dir = os.path.join(sdir, name)
            for root, _dirs, files in os.walk(root_dir):
                for file_name in files:
                    if file_name == "session.lock":
                        continue
                    path = os.path.join(root, file_name)
                    zf.write(path, os.path.relpath(path, sdir))
    return dest_path
