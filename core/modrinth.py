"""Recherche et telechargement de mods/plugins depuis Modrinth (https://modrinth.com).

API publique v2, sans cle. On filtre par loader et version de jeu.
"""
import requests

from .downloaders import download

API = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "MZ-Minecraft-Launcher/1.0 (perso)"}

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


def best_file_url(slug, loader, game_version):
    """Trouve le .jar a telecharger pour un projet (version compatible la plus recente)."""
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


def install(slug, loader, game_version, dest_dir, log=print):
    """Telecharge le mod/plugin dans dest_dir. Renvoie le nom de fichier."""
    import os
    url, filename = best_file_url(slug, loader, game_version)
    download(url, os.path.join(dest_dir, filename), log=log)
    return filename
