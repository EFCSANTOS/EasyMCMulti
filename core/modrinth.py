"""Browse, inspect and install mods/plugins from Modrinth (https://modrinth.com).

Public v2 API, no key required. Results are filtered by loader and game version and limited to
projects that work on a server (client-only mods are hidden). Installing also installs required
dependencies. Mods installed from here are recorded in <mods folder>/.ez-modrinth.json so the
browser can mark them as installed.
"""
import json
import os
import re
import time

import requests

from . import fsutil
from .downloaders import download

API = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "EZ-Minecraft-Launcher/1.0 (personal)"}
RECORD_FILE = ".ez-modrinth.json"
SORTS = ("relevance", "downloads", "follows", "newest", "updated")

# launcher loader -> matching Modrinth loaders
_LOADER_MAP = {
    "fabric": ["fabric"],
    "forge": ["forge"],
    "neoforge": ["neoforge"],
    "paper": ["paper", "spigot", "bukkit", "purpur"],
}

_categories_cache = {"at": 0.0, "items": []}


def _get(path, params=None):
    r = requests.get(f"{API}{path}", params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def _hit(h):
    return {
        "id": h.get("project_id"),
        "slug": h["slug"],
        "title": h["title"],
        "author": h.get("author", ""),
        "description": h.get("description", ""),
        "icon_url": h.get("icon_url") or "",
        "downloads": h.get("downloads", 0),
        "follows": h.get("follows", 0),
        "updated": h.get("date_modified", ""),
        "categories": h.get("display_categories") or h.get("categories", []),
        "server_side": h.get("server_side", ""),
        "client_side": h.get("client_side", ""),
        "gallery": (h.get("gallery") or [])[:4],
    }


def search(query, loader, game_version, limit=20, offset=0, sort="relevance", category=""):
    """Search (or browse with an empty query). Returns {hits, total, offset, limit}."""
    loaders = _LOADER_MAP.get(loader, [])
    facets = [["project_type:mod", "project_type:plugin"], ["server_side:required", "server_side:optional"]]
    if loaders:
        facets.append([f"categories:{l}" for l in loaders])
    if game_version:
        facets.append([f"versions:{game_version}"])
    if category:
        facets.append([f"categories:{category}"])
    if sort not in SORTS:
        sort = "relevance"
    if not query and sort == "relevance":
        sort = "downloads"  # browsing without a search term: most popular first
    params = {"query": query or "", "limit": limit, "offset": offset, "index": sort, "facets": json.dumps(facets)}
    data = _get("/search", params)
    return {
        "hits": [_hit(h) for h in data.get("hits", [])],
        "total": data.get("total_hits", 0),
        "offset": data.get("offset", offset),
        "limit": data.get("limit", limit),
    }


def categories():
    """Mod/plugin categories for the filter (cached for a day)."""
    if _categories_cache["items"] and time.time() - _categories_cache["at"] < 86400:
        return _categories_cache["items"]
    items = sorted({c["name"] for c in _get("/tag/category")
                    if c.get("project_type") in ("mod", "plugin") and c.get("header") == "categories"})
    _categories_cache.update(at=time.time(), items=items)
    return items


def _plain_text(markdown, limit=2500):
    """Readable excerpt of a Markdown/HTML project description (no HTML is rendered in the UI)."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown or "")            # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)                  # links -> text
    text = re.sub(r"<[^>]+>", "", text)                                   # HTML tags
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)      # headings
    text = re.sub(r"[*_`~]{1,3}", "", text)                                # emphasis/code marks
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _compatible_versions(project_id, loader, game_version):
    loaders = _LOADER_MAP.get(loader, [])
    params = {}
    if loaders:
        params["loaders"] = json.dumps(loaders)
    if game_version:
        params["game_versions"] = json.dumps([game_version])
    return _get(f"/project/{project_id}/version", params)


def project(project_ref, loader, game_version):
    """Details for the detail view: links, gallery, description excerpt, compatible versions."""
    p = _get(f"/project/{project_ref}")
    versions = _compatible_versions(p["id"], loader, game_version)
    return {
        "id": p["id"],
        "slug": p["slug"],
        "title": p["title"],
        "description": p.get("description", ""),
        "body": _plain_text(p.get("body", "")),
        "icon_url": p.get("icon_url") or "",
        "downloads": p.get("downloads", 0),
        "follows": p.get("followers", 0),
        "updated": p.get("updated", ""),
        "categories": p.get("categories", []),
        "license": (p.get("license") or {}).get("name") or (p.get("license") or {}).get("id", ""),
        "server_side": p.get("server_side", ""),
        "client_side": p.get("client_side", ""),
        "links": {k: p.get(k) for k in ("source_url", "wiki_url", "discord_url", "issues_url") if p.get(k)},
        "page_url": f"https://modrinth.com/{p.get('project_type', 'mod')}/{p['slug']}",
        "gallery": [{"url": g.get("url"), "title": g.get("title") or ""} for g in (p.get("gallery") or [])[:6]],
        "compatible_versions": len(versions),
        "latest_version": versions[0]["version_number"] if versions else "",
    }


def resolve_project_slug(project_ref):
    """Accepts either a project slug or project UUID/ID and resolves it to the canonical slug."""
    ref = str(project_ref or "").strip()
    if not ref:
        raise ValueError("A Modrinth project identifier is required.")

    r = requests.get(f"{API}/project/{ref}", headers=HEADERS, timeout=30)
    if r.status_code == 200:
        payload = r.json()
        slug = payload.get("slug") if isinstance(payload, dict) else None
        if slug:
            return slug
    return ref


# ---------- Installed record ----------
def load_record(dest_dir):
    data = fsutil.read_json(os.path.join(dest_dir, RECORD_FILE), {})
    return data if isinstance(data, dict) else {}


def _save_record(dest_dir, record):
    fsutil.write_json_atomic(os.path.join(dest_dir, RECORD_FILE), record)


def installed_projects(dest_dir):
    """{project_id: {slug, title, filename}} for jars installed from Modrinth that still exist."""
    return {pid: info for pid, info in load_record(dest_dir).items()
            if os.path.exists(os.path.join(dest_dir, info.get("filename", "")))}


def forget_file(dest_dir, filename):
    record = load_record(dest_dir)
    kept = {pid: info for pid, info in record.items() if info.get("filename") != filename}
    if kept != record:
        _save_record(dest_dir, kept)


def install(project_ref, loader, game_version, dest_dir, log=print):
    """Download the newest compatible version plus its required dependencies. Returns the file name."""
    record = installed_projects(dest_dir)
    seen = set()

    def install_one(ref, required_by=None):
        p = _get(f"/project/{ref}")
        if p["id"] in seen:
            return None
        seen.add(p["id"])
        if p["id"] in record:
            log(f"{p['title']} is already installed.")
            return record[p["id"]]["filename"]
        versions = _compatible_versions(p["id"], loader, game_version)
        if not versions:
            raise RuntimeError(f"{p['title']} has no version for this loader and game version.")
        version = versions[0]  # the newest
        files = version["files"]
        primary = next((f for f in files if f.get("primary")), files[0])
        if required_by:
            log(f"Installing dependency {p['title']} (required by {required_by})...")
        download(primary["url"], os.path.join(dest_dir, primary["filename"]), log=log)
        record[p["id"]] = {"slug": p["slug"], "title": p["title"], "filename": primary["filename"],
                           "version": version.get("version_number", "")}
        for dependency in version.get("dependencies", []):
            if dependency.get("dependency_type") == "required" and dependency.get("project_id"):
                try:
                    install_one(dependency["project_id"], p["title"])
                except (RuntimeError, requests.RequestException) as exc:
                    log(f"Warning: could not install a dependency of {p['title']}: {exc}")
        return primary["filename"]

    try:
        return install_one(project_ref)
    finally:
        _save_record(dest_dir, record)
