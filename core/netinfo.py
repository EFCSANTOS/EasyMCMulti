"""Infos reseau : IP locale (LAN), IP publique, et verification du port du serveur."""
import os
import socket
import subprocess

import requests

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def port_in_use(port):
    """True si un programme ecoute deja sur ce port TCP (serveur deja lance ?)."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def pids_on_port(port):
    """Renvoie l'ensemble des PID qui ECOUTENT sur ce port (via netstat, Windows)."""
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="ignore", timeout=10,
                             creationflags=_CREATE_NO_WINDOW).stdout or ""
    except Exception:
        return pids
    needle = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        # parts: [proto, local_addr, foreign_addr, state, pid]
        if len(parts) >= 5 and parts[1].endswith(needle) and parts[-1].isdigit():
            pids.add(parts[-1])
    return pids


def free_port(port):
    """Tue le(s) programme(s) qui occupent le port. Renvoie True si au moins un tue."""
    killed = False
    for pid in pids_on_port(port):
        try:
            subprocess.run(["taskkill", "/f", "/pid", pid], capture_output=True,
                           timeout=10, creationflags=_CREATE_NO_WINDOW)
            killed = True
        except Exception:
            pass
    return killed


def lan_ip():
    """IP locale du PC sur le reseau (a donner aux potes en LAN / meme wifi)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # pas de vrai trafic, juste pour trouver l'interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def public_ip():
    """IP publique (a donner aux potes hors reseau, avec le port ouvert sur la box)."""
    try:
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except requests.RequestException:
        return "indisponible"
