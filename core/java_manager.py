"""Find or download the right Java version for a Minecraft version.

Windows: the user does not have to install ANYTHING: a portable Eclipse Temurin JRE is
downloaded into EZ_Server_Data/java and used directly.
Linux / Termux (Android): an installed Java is used (Termux: `pkg install openjdk-21`).
Temurin's Linux builds need glibc and cannot run on Android, so nothing is downloaded there.
Each Minecraft version needs a specific Java.
"""
import glob
import os
import re
import shutil
import subprocess

import requests

from . import hostenv, paths

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


def java_major(java_exe):
    """Major version reported by `java -version` (8 for 1.8.x), or None if it cannot run."""
    try:
        out = subprocess.run([java_exe, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not match:
        return None
    major = int(match.group(1))
    return int(match.group(2) or 0) if major == 1 else major


def _system_java_candidates():
    candidates = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(os.path.join(java_home, "bin", "java"))
    if hostenv.is_termux():
        candidates += sorted(glob.glob(os.path.join(hostenv.termux_prefix(), "lib", "jvm", "*", "bin", "java")))
    candidates += sorted(glob.glob("/usr/lib/jvm/*/bin/java"))
    on_path = shutil.which("java")
    if on_path:
        candidates.append(on_path)
    unique = []
    for path in candidates:
        real = os.path.realpath(path)
        if os.path.isfile(real) and real not in unique:
            unique.append(real)
    return unique


def find_system_java(feature):
    """Pick an installed Java: exactly `feature` if present, else the oldest newer one."""
    found = {}
    for exe in _system_java_candidates():
        major = java_major(exe)
        if major is not None:
            found.setdefault(major, exe)
    if feature in found:
        return found[feature], feature
    newer = sorted(major for major in found if major > feature)
    if newer:
        return found[newer[0]], newer[0]
    return None, None


def _ensure_system_java(feature, log):
    exe, major = find_system_java(feature)
    if exe:
        if major != feature:
            log(f"Java {feature} not installed; using Java {major} instead.")
        else:
            log(f"Using installed Java {feature}.")
        return exe
    if hostenv.is_termux():
        hint = f"Install it in Termux with: pkg install openjdk-{feature}"
    else:
        hint = f"Install Java {feature} (e.g. openjdk-{feature}-jre-headless) or set JAVA_HOME."
    raise RuntimeError(f"Java {feature} or newer is required for this Minecraft version. {hint}")


def ensure_java(feature, log=print, progress=None):
    """Return a java executable for the requested feature version.

    Windows downloads a portable JRE if needed; other platforms use an installed Java.
    """
    if not hostenv.is_windows():
        return _ensure_system_java(feature, log)

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
