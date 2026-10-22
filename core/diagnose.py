"""Explain why the Minecraft server crashed or would not start (FEAT-08).

`diagnose(lines, crash_report)` scans recent console output and the newest crash report for known
failure patterns and returns what went wrong and what to do, most specific first. Pure function:
no I/O, so it is easy to test with real log snippets.
Each item: {code, title, message, action} where action is None or {kind, ...}:
  install_mod {slug} · remove_mod {file} · open_setting {page, field} · accept_eula · kill_process
"""
import re

_JAVA_CLASS = re.compile(r"class file version (\d+)(?:\.\d+)?\D.*?up to (\d+)(?:\.\d+)?", re.IGNORECASE)
_FABRIC_MISSING = re.compile(
    r"Mod '([^']+)' \(([\w.-]+)\) \S+ requires (?:any version|version [^,]+?|[^,]*?) of "
    r"(?:mod '([^']+)' \(([\w.-]+)\)|([\w.-]+)), which is missing")
_FORGE_MISSING = re.compile(r"Mod ID: '([\w.-]+)', Requested by: '([\w.-]+)', Expected range: '([^']*)', "
                            r"Actual version: '([^']*)'")
_PORT = re.compile(r"Starting Minecraft server on [^:\s]*:(\d+)")
_MOD_JAR = re.compile(r"mods[/\\]([^/\\\s'\"]+\.jar)")
_MOD_ID = re.compile(r"(?:ModID|Mod ID|modid)[:=]\s*'?([\w.-]+)", re.IGNORECASE)


def _java_major(class_version):
    return int(class_version) - 44  # class file 61 = Java 17, 65 = Java 21


def diagnose(lines, crash_report=""):
    text = "\n".join(lines) + "\n" + (crash_report or "")
    found = []
    seen = set()

    def add(code, title, message, action=None, key=None):
        if (key or code) in seen:
            return
        seen.add(key or code)
        found.append({"code": code, "title": title, "message": message, "action": action})

    for match in _FABRIC_MISSING.finditer(text):
        mod, _mod_id, dep_title, dep_id, bare_id = match.groups()
        dep_id = dep_id or bare_id
        add("missing_dependency", f"{mod} needs {dep_title or dep_id}",
            f"The mod {mod} requires {dep_title or dep_id} ({dep_id}), which is not installed.",
            {"kind": "install_mod", "slug": dep_id}, key=f"dep:{dep_id}")
    for match in _FORGE_MISSING.finditer(text):
        dep_id, requested_by, expected, actual = match.groups()
        if actual.upper() == "[MISSING]":
            message = f"The mod {requested_by} requires {dep_id} ({expected or 'any version'}), which is not installed."
        else:
            message = f"The mod {requested_by} requires {dep_id} {expected}, but {actual} is installed."
        add("missing_dependency", f"{requested_by} needs {dep_id}", message,
            {"kind": "install_mod", "slug": dep_id}, key=f"dep:{dep_id}")
    if "Incompatible mods found" in text or "Incompatible mod set" in text:
        add("incompatible_mods", "Some mods don't work together",
            "The mod loader refused the installed mods. The lines above in the console name the mods involved.")

    if "invalid dist DEDICATED_SERVER" in text or re.search(r"Environment type SERVER is (?:not supported|incompatible)", text):
        jar = _MOD_JAR.search(text)
        mod_id = _MOD_ID.search(text)
        name = jar.group(1) if jar else (mod_id.group(1) if mod_id else "a mod")
        add("client_only_mod", f"{name} only works in the game client",
            f"{name} is a client-side mod (shaders, minimaps, menus…). Servers can't load it: remove it from the server.",
            {"kind": "remove_mod", "file": jar.group(1)} if jar else None)

    java = _JAVA_CLASS.search(text)
    if java or "UnsupportedClassVersionError" in text:
        if java:
            need, have = _java_major(java.group(1)), _java_major(java.group(2))
            message = f"This server or one of its mods needs Java {need}, but Java {have} is running."
        else:
            message = "This server or one of its mods needs a newer Java than the one running."
        add("wrong_java", "Wrong Java version", message + " Check that the Minecraft version matches the mods.")

    if "java.lang.OutOfMemoryError" in text:
        add("out_of_memory", "The server ran out of memory",
            "Give it more RAM in Server setup (and fewer or lighter mods if it is already high).",
            {"kind": "open_setting", "page": "setup", "field": "ram-input"})

    if "FAILED TO BIND TO PORT" in text or "Address already in use" in text:
        port = _PORT.search(text)
        add("port_in_use", f"Port {port.group(1) if port else ''} is already in use".replace("  ", " "),
            "Another program, often a server that didn't shut down, is using the server port. "
            "Stop it, or change the port in Server setup.", {"kind": "kill_process", "port": int(port.group(1))}
            if port else {"kind": "open_setting", "page": "setup", "field": "port-input"})

    if "session.lock" in text and ("already locked" in text or "LevelStorageException" in text):
        add("world_locked", "The world is open in another server",
            "Another Minecraft server is using this world folder. Stop the other one (or restart this device) and try again.")

    if "You need to agree to the EULA" in text:
        add("eula", "The Minecraft EULA is not accepted",
            "The server needs eula=true in eula.txt.", {"kind": "accept_eula"})
    return found


def first_line(items):
    """One-line summary for alerts, or ""."""
    return f"Likely cause: {items[0]['title']}. {items[0]['message']}" if items else ""
