"""Automatic download of the right Java version (Eclipse Temurin / Adoptium).

The user does not have to install ANYTHING: a portable JRE is downloaded into
EZ_Server_Data/java and used directly. Each Minecraft version needs a specific Java.
"""
import os

import requests

from . import paths

# Adoptium endpoint: redirects to the right Windows x64 JRE zip for the requested "feature version".
ADOPTIUM = (
    "https://api.adoptium.net/v3/binary/latest/{feature}/ga/"
    "windows/x64/jre/hotspot/normal/eclipse?project=jdk"
)


def required_java(mc_version):
    """Return the Java version (int) required for a given Minecraft version.

    - new 2026+ scheme (e.g. 26.2)  -> Java 25 (class file 69)
    - 1.20.5+ and 1.21+  -> Java 21
    - 1.18 to 1.20.4     -> Java 17
    - 1.17.x             -> Java 17 (16 minimum, 17 works)
    - 1.16 and earlier   -> Java 8
    Unknown versions / snapshots -> 25 (the most recent).
    """
    core = mc_version.split("-")[0]  # strip any suffix (e.g. pre-release)
    parts = core.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return 25  # snapshot or unexpected format: use the most recent

    if major != 1:
        return 25  # new versioning scheme (2026+): compiled for Java 25
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
    """Find an already installed java.exe for this feature version, otherwise None."""
    d = _install_dir(feature)
    if not os.path.isdir(d):
        return None
    for root, _dirs, files in os.walk(d):
        if "java.exe" in files and os.path.basename(root).lower() == "bin":
            return os.path.join(root, "java.exe")
    return None


def ensure_java(feature, log=print, progress=None):
    """Ensure the requested JRE is present, download it if needed, return the java.exe path."""
    exe = find_java_exe(feature)
    if exe:
        log(f"Java {feature} already present.")
        return exe

    paths.ensure_dirs()
    dest = _install_dir(feature)
    os.makedirs(dest, exist_ok=True)
    zip_path = os.path.join(dest, "jre.zip")

    log(f"Downloading Java {feature} (Temurin)...")
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

    log("Extracting Java...")
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    os.remove(zip_path)

    exe = find_java_exe(feature)
    if not exe:
        raise RuntimeError("java.exe not found after extraction.")
    log(f"Java {feature} installed.")
    return exe
