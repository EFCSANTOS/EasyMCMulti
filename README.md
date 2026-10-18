# MZ Minecraft Server Launcher

> One-click, self-hosted Minecraft server launcher for Windows. It auto-installs the
> correct Java runtime and the server itself, then lets you configure and operate the
> server from a single window — no command line, no manual setup.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-Tkinter-1f6feb)
![License](https://img.shields.io/badge/License-All%20rights%20reserved-red)

*(English first — [version française plus bas](#version-française).)*

---

## Overview

Hosting a Minecraft server normally means installing the right Java version by hand,
downloading the correct server `.jar`, editing config files, and opening ports. This
launcher does all of it for you: pick a server type and a version, click once, and the
launcher downloads a portable JRE and the server, writes the configuration, and starts it.

It ships as a **single portable `.exe`** — copy one file to the host machine and run it.
Everything else is downloaded on first launch into a folder next to the executable.

## Features

- **Guided setup wizard** — a beginner-friendly question/answer flow (server type, version,
  world, game mode, access) that creates the server for you, plus an **advanced mode** with
  the full tabbed interface.
- **One-click remote play** — fully automated **playit.gg** tunnel: a single "Allow" click in
  the browser, then the app fetches the key and **creates the Minecraft tunnel by itself** and
  shows the address to share — no port forwarding, no manual setup on the playit site.
- **Automatic Java** — downloads the correct Eclipse Temurin JRE for each Minecraft
  version (Java 8 / 17 / 21 / 25, incl. the new `26.x` versioning), fully portable.
- **5 server types** — Vanilla, Paper, Fabric, Forge and NeoForge, with live version lists
  fetched from each project's official API.
- **Server configuration UI** — seed, game mode, difficulty, hardcore, world type
  (normal / superflat / large biomes / amplified), max players, render & simulation
  distance, PVP, monsters, nether, flight, command blocks, whitelist, online mode.
- **Mods & plugins** — add/remove local `.jar` files and search/install directly from
  **Modrinth**, filtered by loader and version.
- **Live admin panel** — op, kick, ban, whitelist, per-player game mode, time, weather,
  broadcasts and world saves while the server is running.
- **World backups** — one-click timestamped `.zip` of the world.
- **Networking** — shows LAN and public addresses with copy buttons and a port-forwarding
  helper (in addition to the automatic tunnel above).
- **Modern UI** — dark "SaaS" theme, guided reactive steps, HiDPI-crisp, scrollable panels.
- **Quality of life** — saved profile, one-click reload of an existing server, live player
  counter, auto-restart on crash, RAM detection with a safety guard, crash reporting.
- **Single-file export** — `build.bat` produces one portable `.exe` via PyInstaller.

## Tech stack

| Area | Choice | Why |
|------|--------|-----|
| Language | **Python 3.13** | Readable, batteries-included, trivial `.exe` export |
| GUI | **Tkinter** (stdlib) | No heavy dependency, ships with Python |
| HTTP | **requests** | Only third-party dependency (downloads + APIs) |
| Packaging | **PyInstaller** (`--onefile`) | One portable executable, zero install on the host |

## Architecture

The GUI orchestrates a small, single-responsibility `core/` package:

```
launcher.py            Tkinter GUI (tabs: Server / Configuration / Mods / Admin) + orchestration
core/
  paths.py             Runtime data locations (next to the .exe)
  java_manager.py      Maps MC version -> Java version, downloads a portable Temurin JRE
  downloaders.py       Version lists, server downloads, Forge/NeoForge installer handling
  server_process.py    Subprocess management, console streaming, eula/properties files
  modrinth.py          Mod/plugin search and install (Modrinth API)
  playit.py            playit.gg agent (remote tunnel)
  netinfo.py           LAN and public IP detection
  sysinfo.py           Total RAM detection (allocation guard)
  config_store.py      JSON profile load/save
  crash_reporter.py    Structured crash reports
```

Each `loader + version` pair gets its own folder, so every world is persistent and
independent.

## Getting started (from source)

Requirements: Windows, Python 3.13+.

```bat
run.bat            :: installs dependencies and launches the GUI
```

Or directly:

```bat
python -m pip install -r requirements.txt
python launcher.py
```

## Build the executable

```bat
build.bat          :: -> dist\MZ_Server_Launcher.exe (single portable file)
```

Copy `dist\MZ_Server_Launcher.exe` to the host machine and run it. No Python or Java
required on the target — both the JRE and the server are fetched on first launch.

## How it works

1. **Prepare** — resolve the required Java version, download a portable JRE, download the
   server `.jar` (or run the Forge/NeoForge installer), then write `eula.txt` and
   `server.properties`.
2. **Start** — build the launch command and run the server as a subprocess, streaming its
   console into the UI and parsing it for the live player count.
3. **Operate** — send admin commands, manage mods, back up the world, copy the connection
   address for your friends.

## Hardware target

Designed for a modest home server (Intel i5-10300 / 16 GB RAM / GTX 1650). ~6 GB allocated
to the server comfortably hosts 8–10 players on Paper.

## License