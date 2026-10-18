"""Recherche et telechargement de mods/plugins depuis Modrinth (https://modrinth.com).

API publique v2, sans cle. On filtre par loader et version de jeu.
"""
import requests

from .downloaders import download

API = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "EZ-Minecraft-Launcher/1.0 (perso)"}

# loader du launcher -> loaders Modrinth correspondants
_LOADER_MAP = {
    "fabric": ["fabric"],
    "forge": ["forge"],
    "neoforge": ["neoforge"],
    "paper": ["paper", "spigot", "bukkit", "purpur"],
}


def search(query, loader, game_version, limit=20):
    """Renvoie une liste de projets : [{title, slug, description, downloads}]."""
    loaders = _LOADER_MAP.get(loader, [])
    facets = [[f"categories:{l}" for l in loaders]] if loaders else []
    if game_version:
        facets.append([f"versions:{game_version}"])
    params = {"query": query, "limit": limit, "index": "relevance"}
    if facets:
        import json
        params["facets"] = json.dumps(facets)
    r = requests.get(f"{API}/search", params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return [
        {
            "title": h["title"],
            "slug": h["slug"],
            "description": h.get("description", ""),
            "downloads": h.get("downloads", 0),
        }
        for h in r.json().get("hits", [])
    ]


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


def best_file_url(project_ref, loader, game_version):
    """Trouve le .jar a telecharger pour un projet (version compatible la plus recente)."""
    slug = resolve_project_slug(project_ref)
    loaders = _LOADER_MAP.get(loader, [])
    import json
    params = {}
    if loaders:
        params["loaders"] = json.dumps(loaders)
    if game_version:
        params["game_versions"] = json.dumps([game_version])
    r = requests.get(f"{API}/project/{slug}/version", params=params,
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    versions = r.json()
    if not versions:
        raise RuntimeError("Aucune version compatible (loader/version de jeu).")
    files = versions[0]["files"]  # la plus recente
    primary = next((f for f in files if f.get("primary")), files[0])
    return primary["url"], primary["filename"]


def install(project_ref, loader, game_version, dest_dir, log=print):
    """Telecharge le mod/plugin dans dest_dir. Renvoie le nom de fichier."""
    import os
    url, filename = best_file_url(project_ref, loader, game_version)
    download(url, os.path.join(dest_dir, filename), log=log)
    return filename
