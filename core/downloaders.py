"""Version lists and server .jar downloads for each loader.

Supported loaders:
- vanilla  : official Mojang server
- paper    : optimized server (Bukkit/Spigot plugins), great for playing with friends
- fabric   : moddable server (Fabric mods)
- forge    : moddable server (Forge mods, installed via the Forge installer)
- neoforge : moddable server (NeoForge mods, installed via the NeoForge installer)
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
JAR_LOADERS = ("vanilla", "paper", "fabric")          # launched with -jar server.jar
INSTALLER_LOADERS = ("forge", "neoforge")             # go through an installer

# Simple cache to avoid re-downloading the vanilla manifest every time.
_vanilla_meta_urls = {}


def download(url, dest, log=print, progress=None):
    """Download url to dest (writes a .part file, then renames it once complete)."""
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
        vanilla_versions(include_snapshots=True)  # fills the cache
        meta_url = _vanilla_meta_urls[version]
    meta = requests.get(meta_url, timeout=30).json()
    server = meta.get("downloads", {}).get("server")
    if not server:
        raise RuntimeError(f"No server available for version {version}.")
    return server["url"]


# ---------- PAPER ----------
def paper_versions():
    data = requests.get(PAPER_API, timeout=30).json()
    return list(reversed(data["versions"]))  # newest first


def paper_server_url(version):
    builds = requests.get(f"{PAPER_API}/versions/{version}/builds", timeout=30).json()["builds"]
    if not builds:
        raise RuntimeError(f"No Paper build for {version}.")
    b = builds[-1]  # latest build
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
    # Fabric serves a ready-to-run server .jar directly.
    return f"{FABRIC_META}/versions/loader/{game_version}/{loader}/{installer}/server/jar"


# ---------- FORGE ----------
def _forge_promos():
    return requests.get(FORGE_PROMOS, timeout=30).json()["promos"]


def forge_versions():
    """Minecraft versions supported by Forge (newest first)."""
    promos = _forge_promos()
    mcs = {k.rsplit("-", 1)[0] for k in promos}  # strip -latest / -recommended
    return sorted(mcs, key=_version_key, reverse=True)


def forge_build(mc_version):
    """Forge build number for an MC version (recommended, otherwise latest)."""
    promos = _forge_promos()
    return promos.get(f"{mc_version}-recommended") or promos.get(f"{mc_version}-latest")


def forge_installer_url(mc_version):
    build = forge_build(mc_version)
    if not build:
        raise RuntimeError(f"No Forge build for {mc_version}.")
    v = f"{mc_version}-{build}"
    return f"{FORGE_MAVEN}/{v}/forge-{v}-installer.jar"


# ---------- NEOFORGE ----------
def _neoforge_all():
    return requests.get(NEOFORGE_VERSIONS, timeout=30).json()["versions"]


def _neoforge_to_mc(ver):
    """Derive the MC version from a NeoForge version.

    Old scheme (1.x): '21.1.73' -> '1.21.1', '21.0.167' -> '1.21'.
    New scheme (>=2026, e.g. MC '26.2'): '26.2.0.7' -> '26.2'.
    """
    a, b = ver.split(".")[:2]
    if int(a) >= 25:  # new year-based Minecraft versioning
        return f"{a}.{b}"
    return f"1.{a}" if b == "0" else f"1.{a}.{b}"


def neoforge_versions():
    mcs = {_neoforge_to_mc(v) for v in _neoforge_all()}
    return sorted(mcs, key=_version_key, reverse=True)


def neoforge_build(mc_version):
    """Latest NeoForge version for the given MC version."""
    matches = [v for v in _neoforge_all() if _neoforge_to_mc(v) == mc_version]
    if not matches:
        raise RuntimeError(f"No NeoForge build for {mc_version}.")
    return sorted(matches, key=_version_key)[-1]


def neoforge_installer_url(mc_version):
    ver = neoforge_build(mc_version)
    return f"{NEOFORGE_MAVEN}/{ver}/neoforge-{ver}-installer.jar"


def _version_key(v):
    """Lenient numeric sort key (e.g. '1.21.10' > '1.21.9')."""
    out = []
    for part in v.replace("-", ".").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return out


# ---------- Forge/NeoForge installation ----------
def _run(args, cwd, log):
    proc = subprocess.Popen(
        args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    for line in proc.stdout:
        log(line.rstrip("\n"))
    return proc.wait()


def _find_args_file(sdir):
    """Modern Forge/NeoForge launch-arguments file (win_args.txt on Windows, unix_args.txt elsewhere)."""
    name = "win_args.txt" if sys.platform == "win32" else "unix_args.txt"
    matches = glob.glob(os.path.join(sdir, "libraries", "**", name), recursive=True)
    return matches[0] if matches else None


def _already_installed(loader, sdir):
    if loader in JAR_LOADERS:
        return os.path.exists(os.path.join(sdir, "server.jar"))
    if _find_args_file(sdir):
        return True
    jars = [j for j in glob.glob(os.path.join(sdir, "forge-*.jar")) if "installer" not in j]
    return bool(jars)


def prepare(loader, version, sdir, java_exe, log=print, progress=None):
    """Download (and, for forge/neoforge, install) the server into sdir."""
    if _already_installed(loader, sdir):
        log("Server already installed.")
        return

    if loader in JAR_LOADERS:
        jar = os.path.join(sdir, "server.jar")
        log("Looking up the server...")
        url = server_jar_url(loader, version)
        log("Downloading the server...")
        download(url, jar, log=log, progress=progress)
        return

    # forge / neoforge: download the installer, then run it
    url = forge_installer_url(version) if loader == "forge" else neoforge_installer_url(version)
    installer = os.path.join(sdir, "installer.jar")
    log("Downloading the installer...")
    download(url, installer, log=log, progress=progress)
    log("Installing the server (this can take a few minutes)...")
    code = _run([java_exe, "-jar", "installer.jar", "--installServer"], sdir, log)
    if code != 0:
        raise RuntimeError(f"The {loader} installer failed (exit code {code}).")
    log("Installation complete.")


def launch_args(loader, java_exe, sdir, ram_mb):
    """Build the full server launch command."""
    base = [java_exe, f"-Xms{ram_mb}M", f"-Xmx{ram_mb}M"]
    if loader in JAR_LOADERS:
        return base + ["-jar", "server.jar", "nogui"]
    args_file = _find_args_file(sdir)
    if args_file:  # modern forge/neoforge: arguments file
        return base + ["@" + os.path.relpath(args_file, sdir), "nogui"]
    jars = [j for j in glob.glob(os.path.join(sdir, "forge-*.jar")) if "installer" not in j]
    if jars:  # old forge: directly runnable jar
        return base + ["-jar", os.path.basename(jars[0]), "nogui"]
    raise RuntimeError("Launch command not found (incomplete installation?).")


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
    raise ValueError(f"Unknown loader: {loader}")


def server_jar_url(loader, version):
    if loader == "vanilla":
        return vanilla_server_url(version)
    if loader == "paper":
        return paper_server_url(version)
    if loader == "fabric":
        return fabric_server_url(version)
    raise ValueError(f"Loader has no direct server jar: {loader}")
