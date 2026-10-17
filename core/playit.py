"""Integration playit.gg : tunnel pour jouer a distance sans ouvrir de port sur la box.

IMPORTANT (agent v1.0.10+) : le binaire Windows de playit est un *daemon* pur. Il faut lui
fournir une SECRET KEY (--secret) ; lance sans secret il attend une provision IPC et se ferme.

Au lieu de demander a l'utilisateur de bricoler le site playit a la main, on AUTOMATISE tout
via l'API officielle (api.playit.gg) :
  1. on genere un "claim code" (5 octets hex) et on ouvre https://playit.gg/claim/<code>
  2. l'utilisateur clique "Allow" dans son navigateur (seule etape manuelle, = un login)
  3. on echange le code contre une SECRET KEY permanente (/claim/exchange)
  4. on cree tout seul un tunnel "Minecraft Java" -> localhost:25565 (/tunnels/create)
  5. on recupere l'adresse publique allouee (/tunnels/list)
  6. on lance le daemon : playit.exe --secret <KEY>

Secret + adresse sont stockes LOCALEMENT (MZ_Server_Data/playit_config.json), jamais commit.

Refs API (client officiel) : https://github.com/playit-cloud/playit-api-java
Auth : header  Authorization: Agent-Key <secret>
"""
import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser

import requests

from . import paths

RELEASES_API = "https://api.github.com/repos/playit-cloud/playit-agent/releases/latest"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

API_BASE = "https://api.playit.gg"
CLAIM_PAGE = "https://playit.gg/claim/{}"
TUNNELS_URL = "https://playit.gg/account/tunnels"  # fallback manuel si la creation auto echoue

_agent_path = os.path.join(paths.DATA_DIR, "playit.exe")
_CONFIG_FILE = os.path.join(paths.DATA_DIR, "playit_config.json")


# ---------- Config locale (secret key + adresse du tunnel) ----------
def load_config():
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(secret, address=""):
    paths.ensure_dirs()
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"secret": secret, "address": address}, f, indent=2)


def is_configured():
    return bool(load_config().get("secret"))


def saved_address():
    return load_config().get("address", "")


# ---------- Appels API playit ----------
def _api(path, body, secret=None):
    """POST JSON vers l'API playit. Leve RuntimeError en cas d'echec applicatif.

    ATTENTION : l'API renvoie souvent un HTTP 400 AVEC un corps JSON utile
    (ex: {"status":"fail","data":"AgentVersionTooOld"}). Il faut donc lire le corps
    AVANT de considerer le code HTTP, sinon on perd le vrai message d'erreur.
    """
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Agent-Key {secret}"
    r = requests.post(API_BASE + path, json=body, headers=headers, timeout=30)
    try:
        data = r.json()
    except ValueError:
        r.raise_for_status()
        raise RuntimeError(f"Reponse playit non-JSON (HTTP {r.status_code}) sur {path}")
    if isinstance(data, dict) and data.get("status") in ("fail", "error"):
        raise RuntimeError(f"API playit {path} : {data.get('data')}")
    if not r.ok:
        raise RuntimeError(f"API playit {path} HTTP {r.status_code} : {data}")
    return data


def claim_generate():
    return secrets.token_hex(5)  # 5 octets -> 10 caracteres hex (format attendu par l'API)


def claim_url(code):
    return CLAIM_PAGE.format(code)


def _claim_setup(code):
    res = _api("/claim/setup", {"code": code, "agent_type": "self-managed",
                                "version": "playit-mz-launcher 1.0"})
    return res.get("data")  # WaitingForUserVisit | WaitingForUser | UserAccepted | UserRejected


def _claim_exchange(code):
    """Renvoie la secret key si dispo, sinon None (claim pas encore valide).

    Tant que l'utilisateur n'a pas approuve, l'API repond 400 (ou status 'fail') :
    on traite ca comme 'pas encore pret' et on laisse la boucle d'attente reessayer.
    """
    try:
        res = _api("/claim/exchange", {"code": code})
    except (requests.HTTPError, RuntimeError):
        return None
    return res.get("data", {}).get("secret_key")


def _agent_id(secret):
    res = _api("/agents/rundata", {}, secret=secret)
    return res.get("data", {}).get("agent_id")


def _find_mc_tunnel(secret):
    res = _api("/tunnels/list", {}, secret=secret)
    for t in res.get("data", {}).get("tunnels", []):
        if t.get("tunnel_type") == "minecraft-java":
            return t
    return None


def _tunnel_address(t):
    """Adresse publique a donner aux joueurs, ou None si pas encore allouee."""
    alloc = t.get("alloc", {})
    if alloc.get("status") != "allocated":
        return None
    d = alloc.get("data", {})
    domain = d.get("assigned_domain")
    if not domain:
        return None
    # minecraft-java a normalement un enregistrement SRV -> le domaine seul suffit.
    if d.get("assigned_srv"):
        return domain
    port = d.get("port_start")
    return f"{domain}:{int(port)}" if port else domain


def claim_interactive(log, should_cancel, timeout_sec=300):
    """Genere un claim, ouvre la page d'autorisation, attend le clic 'Allow', renvoie le secret.

    Sauvegarde le secret des qu'il est obtenu (l'adresse du tunnel viendra apres).
    """
    code = claim_generate()
    url = claim_url(code)
    log(f"Ouverture de la page d'autorisation playit : {url}")
    webbrowser.open(url)
    log("Clique 'Allow' dans ton navigateur pour autoriser le launcher...")

    deadline = time.time() + timeout_sec
    while True:
        if should_cancel():
            raise RuntimeError("Configuration annulee.")
        if time.time() > deadline:
            raise RuntimeError("Delai depasse : autorisation non validee dans les temps.")
        state = _claim_setup(code)
        if state == "UserAccepted":
            break
        if state == "UserRejected":
            raise RuntimeError("Autorisation refusee dans le navigateur.")
        time.sleep(1.5)

    log("Autorise. Recuperation de la cle...")
    secret = None
    while secret is None:
        if time.time() > deadline:
            raise RuntimeError("Delai depasse a l'echange de la cle.")
        secret = _claim_exchange(code)
        if secret is None:
            time.sleep(1.5)

    save_config(secret, saved_address())
    log("Cle playit obtenue.")
    return secret


def ensure_tunnel(secret, local_port, log, timeout_sec=120):
    """Garantit un tunnel Minecraft Java -> localhost:local_port et renvoie l'adresse publique.

    IMPORTANT : l'API refuse de creer un tunnel tant que l'agent n'a pas tourne au moins
    une fois (erreur 'AgentVersionTooOld'). Il faut donc avoir demarre le daemon AVANT
    d'appeler cette fonction ; on reessaie le temps que l'agent s'enregistre.
    """
    deadline = time.time() + timeout_sec
    agent_id = _agent_id(secret)

    # Reutilise un tunnel Minecraft existant s'il pointe deja vers le bon port local.
    t = _find_mc_tunnel(secret)
    if t is not None:
        existing = t.get("origin", {}).get("data", {}).get("local_port")
        if existing != local_port:
            log(f"Tunnel existant pointe vers :{existing}, recreation vers :{local_port}...")
            _api("/tunnels/delete", {"tunnel_id": t["id"]}, secret=secret)
            t = None

    if t is None:
        log(f"Creation du tunnel Minecraft Java -> localhost:{local_port}...")
        while True:
            try:
                _api("/tunnels/create", {
                    "name": "MZ Minecraft",
                    "tunnel_type": "minecraft-java",
                    "port_type": "tcp",
                    "port_count": 1,
                    "origin": {"type": "agent", "data": {
                        "agent_id": agent_id, "local_ip": "127.0.0.1",
                        "local_port": local_port}},
                    "enabled": True,
                }, secret=secret)
                break
            except RuntimeError as e:
                if "AgentVersionTooOld" in str(e) and time.time() < deadline:
                    log("Attente de l'enregistrement de l'agent playit...")
                    time.sleep(3)
                    continue
                raise

    log("Attente de l'attribution de l'adresse par playit...")
    while True:
        if time.time() > deadline:
            raise RuntimeError("Le tunnel n'a pas recu d'adresse a temps.")
        t = _find_mc_tunnel(secret)
        if t:
            addr = _tunnel_address(t)
            if addr:
                return addr
        time.sleep(2)


# ---------- Telechargement de l'agent (daemon) ----------
def ensure_agent(log=print):
    """Telecharge l'agent playit (binaire Windows x64 signe) s'il n'est pas la. Renvoie le chemin."""
    if os.path.exists(_agent_path):
        return _agent_path
    paths.ensure_dirs()
    log("Recherche de l'agent playit.gg...")
    rel = requests.get(RELEASES_API, timeout=30).json()
    asset = None
    for a in rel.get("assets", []):
        name = a["name"].lower()
        if name.endswith(".exe") and "windows" in name and "x86_64" in name:
            asset = a
            if "signed" in name:
                break  # on prefere le binaire signe
    if not asset:
        raise RuntimeError("Binaire Windows playit introuvable dans la derniere release.")
    log(f"Telechargement de {asset['name']}...")
    from .downloaders import download
    download(asset["browser_download_url"], _agent_path, log=log)
    return _agent_path


# ---------- Lancement du daemon ----------
class PlayitAgent:
    """Lance le daemon playit avec la secret key et stream sa sortie."""

    def __init__(self, on_output, on_exit):
        self.on_output = on_output
        self.on_exit = on_exit
        self.proc = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, secret):
        if not secret:
            raise RuntimeError("Aucune secret key playit configuree.")
        self.proc = subprocess.Popen(
            [_agent_path, "--secret", secret],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            self.on_output(line.rstrip("\n"))
        self.on_exit(self.proc.wait())

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
