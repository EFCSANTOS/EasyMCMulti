"""Listes de versions et telechargement des .jar serveur pour chaque loader.

Loaders supportes :
- vanilla : serveur officiel Mojang
- paper   : serveur optimise (plugins Bukkit/Spigot), tres bon pour jouer entre potes
- fabric  : serveur moddable (mods Fabric)
"""
import glob
import os
import subprocess
import sys

import requests

VANILLA_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
PAPER_API = "https://api.papermc.io/v2/projects/paper"
FABRIC_META = "https://meta.fabricmc.net/v2"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
JAR_LOADERS = ("vanilla", "paper", "fabric")          # se lancent avec -jar server.jar
INSTALLER_LOADERS = ("forge", "neoforge")             # passent par un installeur

# Cache simple pour eviter de re-telecharger le manifest vanilla a chaque fois.
_vanilla_meta_urls = {}


def download(url, dest, log=print, progress=None):
    """Telecharge url vers dest (fichier .part puis renomme une fois complet)."""
    tmp = dest + ".part"
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done, total)
    os.replace(tmp, dest)
    return dest


# ---------- VANILLA ----------
def vanilla_versions(include_snapshots=False):
    data = requests.get(VANILLA_MANIFEST, timeout=30).json()
    out = []
    for v in data["versions"]:
        if v["type"] == "release" or (include_snapshots and v["type"] == "snapshot"):
            out.append(v["id"])
        _vanilla_meta_urls[v["id"]] = v["url"]
    return out


def vanilla_server_url(version):
    meta_url = _vanilla_meta_urls.get(version)
    if not meta_url:
        vanilla_versions(include_snapshots=True)  # remplit le cache
        meta_url = _vanilla_meta_urls[version]
    meta = requests.get(meta_url, timeout=30).json()
    server = meta.get("downloads", {}).get("server")
    if not server:
        raise RuntimeError(f"Pas de serveur disponible pour la version {version}.")
    return server["url"]


# ---------- PAPER ----------
def paper_versions():
    data = requests.get(PAPER_API, timeout=30).json()
    return list(reversed(data["versions"]))  # plus recentes en premier


def paper_server_url(version):
    builds = requests.get(f"{PAPER_API}/versions/{version}/builds", timeout=30).json()["builds"]
    if not builds:
        raise RuntimeError(f"Aucun build Paper pour {version}.")
    b = builds[-1]  # dernier build
    name = b["downloads"]["application"]["name"]
    return f"{PAPER_API}/versions/{version}/builds/{b['build']}/downloads/{name}"


# ---------- FABRIC ----------
def fabric_versions():
    games = requests.get(f"{FABRIC_META}/versions/game", timeout=30).json()
    return [g["version"] for g in games if g["stable"]]


def fabric_server_url(game_version):
    loaders = requests.get(f"{FABRIC_META}/versions/loader", timeout=30).json()
    loader = next(l["version"] for l in loaders if l["stable"])
    installers = requests.get(f"{FABRIC_META}/versions/installer", timeout=30).json()
    installer = next(i["version"] for i in installers if i["stable"])
    # Fabric fournit directement un .jar serveur pret a lancer.
    return f"{FABRIC_META}/versions/loader/{game_version}/{loader}/{installer}/server/jar"


# ---------- FORGE ----------
def _forge_promos():
    return requests.get(FORGE_PROMOS, timeout=30).json()["promos"]


def forge_versions():
    """Liste des versions Minecraft supportees par Forge (plus recentes en premier)."""
    promos = _forge_promos()
    mcs = {k.rsplit("-", 1)[0] for k in promos}  # enleve -latest / -recommended
    return sorted(mcs, key=_version_key, reverse=True)


def forge_build(mc_version):
    """Numero de build Forge pour une version MC (recommended sinon latest)."""
    promos = _forge_promos()
    return promos.get(f"{mc_version}-recommended") or promos.get(f"{mc_version}-latest")


def forge_installer_url(mc_version):
    build = forge_build(mc_version)
    if not build:
        raise RuntimeError(f"Pas de build Forge pour {mc_version}.")
    v = f"{mc_version}-{build}"
    return f"{FORGE_MAVEN}/{v}/forge-{v}-installer.jar"


# ---------- NEOFORGE ----------
def _neoforge_all():
    return requests.get(NEOFORGE_VERSIONS, timeout=30).json()["versions"]


def _neoforge_to_mc(ver):
    """Deduit la version MC depuis la version NeoForge.

    Ancien schema (1.x) : '21.1.73' -> '1.21.1', '21.0.167' -> '1.21'.
    Nouveau schema (>=2026, ex MC '26.2') : '26.2.0.7' -> '26.2'.
    """
    a, b = ver.split(".")[:2]
    if int(a) >= 25:  # nouveau versioning Minecraft (annee-based)
        return f"{a}.{b}"
    return f"1.{a}" if b == "0" else f"1.{a}.{b}"


def neoforge_versions():
    mcs = {_neoforge_to_mc(v) for v in _neoforge_all()}
    return sorted(mcs, key=_version_key, reverse=True)


def neoforge_build(mc_version):
    """Derniere version NeoForge pour la version MC donnee."""
    matches = [v for v in _neoforge_all() if _neoforge_to_mc(v) == mc_version]
    if not matches:
        raise RuntimeError(f"Pas de build NeoForge pour {mc_version}.")
    return sorted(matches, key=_version_key)[-1]


def neoforge_installer_url(mc_version):
    ver = neoforge_build(mc_version)
    return f"{NEOFORGE_MAVEN}/{ver}/neoforge-{ver}-installer.jar"


def _version_key(v):
    """Cle de tri numerique tolerante (ex '1.21.10' > '1.21.9')."""
    out = []
    for part in v.replace("-", ".").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return out


# ---------- Installation Forge/NeoForge ----------
def _run(args, cwd, log):
    proc = subprocess.Popen(
        args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    for line in proc.stdout:
        log(line.rstrip("\n"))
    return proc.wait()


def _find_args_file(sdir):
    matches = glob.glob(os.path.join(sdir, "libraries", "**", "win_args.txt"), recursive=True)
    return matches[0] if matches else None


def _already_installed(loader, sdir):
    if loader in JAR_LOADERS:
        return os.path.exists(os.path.join(sdir, "server.jar"))
    if _find_args_file(sdir):
        return True
    jars = [j for j in glob.glob(os.path.join(sdir, "forge-*.jar")) if "installer" not in j]
    return bool(jars)


def prepare(loader, version, sdir, java_exe, log=print, progress=None):
    """Telecharge (et installe pour forge/neoforge) le serveur dans sdir."""
    if _already_installed(loader, sdir):
        log("Serveur deja installe.")
        return

    if loader in JAR_LOADERS:
        jar = os.path.join(sdir, "server.jar")
        log("Recherche du serveur...")
        url = server_jar_url(loader, version)
        log("Telechargement du serveur...")
        download(url, jar, log=log, progress=progress)
        return

    # forge / neoforge : telecharger l'installeur puis l'executer
    url = forge_installer_url(version) if loader == "forge" else neoforge_installer_url(version)
    installer = os.path.join(sdir, "installer.jar")
    log("Telechargement de l'installeur...")
    download(url, installer, log=log, progress=progress)
    log("Installation du serveur (peut prendre quelques minutes)...")
    code = _run([java_exe, "-jar", "installer.jar", "--installServer"], sdir, log)
    if code != 0:
        raise RuntimeError(f"L'installeur {loader} a echoue (code {code}).")
    log("Installation terminee.")


def launch_args(loader, java_exe, sdir, ram_mb):
    """Construit la commande complete de lancement du serveur."""
    base = [java_exe, f"-Xms{ram_mb}M", f"-Xmx{ram_mb}M"]
    if loader in JAR_LOADERS:
        return base + ["-jar", "server.jar", "nogui"]
    args_file = _find_args_file(sdir)
    if args_file:  # forge/neoforge modernes : fichier d'arguments
        return base + ["@" + os.path.relpath(args_file, sdir), "nogui"]
    jars = [j for j in glob.glob(os.path.join(sdir, "forge-*.jar")) if "installer" not in j]
    if jars:  # vieux forge : jar lancable directement
        return base + ["-jar", os.path.basename(jars[0]), "nogui"]
    raise RuntimeError("Commande de lancement introuvable (installation incomplete ?).")


# ---------- Dispatch ----------
def list_versions(loader, include_snapshots=False):
    if loader == "vanilla":
        return vanilla_versions(include_snapshots)
    if loader == "paper":
        return paper_versions()
    if loader == "fabric":
        return fabric_versions()
    if loader == "forge":
        return forge_versions()
    if loader == "neoforge":
        return neoforge_versions()
    raise ValueError(f"Loader inconnu : {loader}")


def server_jar_url(loader, version):
    if loader == "vanilla":
        return vanilla_server_url(version)
    if loader == "paper":
        return paper_server_url(version)
    if loader == "fabric":
        return fabric_server_url(version)
    raise ValueError(f"Loader sans jar direct : {loader}")
