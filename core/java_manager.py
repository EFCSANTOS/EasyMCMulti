"""Telechargement automatique de la bonne version de Java (Eclipse Temurin / Adoptium).

L'utilisateur n'a RIEN a installer : on telecharge un JRE portable dans MZ_Server_Data/java
et on l'utilise directement. Chaque version de Minecraft a besoin d'un Java precis.
"""
import os

import requests

from . import paths

# Endpoint Adoptium : redirige vers le bon zip JRE Windows x64 pour la "feature version" demandee.
ADOPTIUM = (
    "https://api.adoptium.net/v3/binary/latest/{feature}/ga/"
    "windows/x64/jre/hotspot/normal/eclipse?project=jdk"
)


def required_java(mc_version):
    """Renvoie la version de Java (int) necessaire pour une version Minecraft donnee.

    - nouveau schema 2026+ (ex. 26.2)  -> Java 25 (class file 69)
    - 1.20.5+ et 1.21+  -> Java 21
    - 1.18 a 1.20.4     -> Java 17
    - 1.17.x            -> Java 17 (16 mini, 17 marche)
    - 1.16 et avant     -> Java 8
    Versions inconnues / snapshots -> 25 (le plus recent).
    """
    core = mc_version.split("-")[0]  # enleve un eventuel suffixe (ex: pre-release)
    parts = core.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return 25  # snapshot ou format inattendu : on prend le plus recent

    if major != 1:
        return 25  # nouveau schema de versioning (2026+) : compile pour Java 25
    if minor >= 21:
        return 21
    if minor == 20:
        return 21 if patch >= 5 else 17
    if minor >= 17:
        return 17
    return 8


def _install_dir(feature):
    return os.path.join(paths.JAVA_DIR, f"jre-{feature}")


def find_java_exe(feature):
    """Cherche java.exe deja installe pour cette feature version, sinon None."""
    d = _install_dir(feature)
    if not os.path.isdir(d):
        return None
    for root, _dirs, files in os.walk(d):
        if "java.exe" in files and os.path.basename(root).lower() == "bin":
            return os.path.join(root, "java.exe")
    return None


def ensure_java(feature, log=print, progress=None):
    """Garantit la presence du JRE demande, le telecharge au besoin, renvoie le chemin java.exe."""
    exe = find_java_exe(feature)
    if exe:
        log(f"Java {feature} deja present.")
        return exe

    paths.ensure_dirs()
    dest = _install_dir(feature)
    os.makedirs(dest, exist_ok=True)
    zip_path = os.path.join(dest, "jre.zip")

    log(f"Telechargement de Java {feature} (Temurin)...")
    url = ADOPTIUM.format(feature=feature)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done, total)

    log("Extraction de Java...")
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    os.remove(zip_path)

    exe = find_java_exe(feature)
    if not exe:
        raise RuntimeError("java.exe introuvable apres extraction.")
    log(f"Java {feature} installe.")
    return exe
