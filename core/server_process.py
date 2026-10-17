"""Lancement et controle du processus serveur Minecraft + fichiers de config (eula, properties)."""
import os
import subprocess
import sys
import threading

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def accept_eula(server_dir):
    """Ecrit eula=true (obligatoire pour que le serveur Mojang demarre)."""
    with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as f:
        f.write("eula=true\n")


def read_properties(server_dir):
    """Lit server.properties et renvoie un dict {cle: valeur} (vide si absent)."""
    path = os.path.join(server_dir, "server.properties")
    props = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v
    return props


def update_properties(server_dir, **kwargs):
    """Met a jour server.properties avec les cles fournies (cree le fichier si absent).

    Les valeurs bool sont converties en true/false. Le serveur complete le reste au demarrage.
    """
    path = os.path.join(server_dir, "server.properties")
    props = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v
    for k, v in kwargs.items():
        key = k.replace("_", "-")
        if isinstance(v, bool):
            v = "true" if v else "false"
        props[key] = str(v)
    with open(path, "w", encoding="utf-8") as f:
        for k, v in props.items():
            f.write(f"{k}={v}\n")


class ServerProcess:
    """Enveloppe autour du sous-processus serveur (commande deja construite).

    args              : liste d'arguments complete (java + options + jar/argsfile + nogui)
    on_output(line)   : appele pour chaque ligne de console du serveur
    on_exit(code)     : appele quand le serveur s'arrete
    """

    def __init__(self, args, cwd, on_output, on_exit):
        self.args = args
        self.cwd = cwd
        self.on_output = on_output
        self.on_exit = on_exit
        self.proc = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        self.proc = subprocess.Popen(
            self.args,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            self.on_output(line.rstrip("\n"))
        code = self.proc.wait()
        self.on_exit(code)

    def send(self, command):
        if self.is_running():
            try:
                self.proc.stdin.write(command + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def stop(self):
        """Arret propre : commande 'stop' (sauvegarde le monde)."""
        self.send("stop")

    def kill(self):
        if self.proc is not None:
            self.proc.kill()
