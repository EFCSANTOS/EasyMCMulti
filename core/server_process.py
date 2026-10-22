"""Minecraft server process launch and control + config files (eula, properties)."""
import os
import subprocess
import sys
import threading

from . import fsutil

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def accept_eula(server_dir):
    """Write eula=true (required for the Mojang server to start)."""
    with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as f:
        f.write("eula=true\n")


def read_properties(server_dir):
    """Read server.properties and return a {key: value} dict (empty if missing)."""
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
    """Update server.properties with the given keys (creates the file if missing).

    Bool values are written as true/false. The server fills in the rest on startup.
    """
    props = read_properties(server_dir)
    for k, v in kwargs.items():
        key = k.replace("_", "-")
        if isinstance(v, bool):
            v = "true" if v else "false"
        props[key] = str(v)
    text = "".join(f"{k}={v}\n" for k, v in props.items())
    fsutil.write_text_atomic(os.path.join(server_dir, "server.properties"), text)


class ServerProcess:
    """Wrapper around the server subprocess (command already built).

    args              : full argument list (java + options + jar/argsfile + nogui)
    on_output(line)   : called for each server console line
    on_exit(code)     : called when the server exits
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
        """Graceful stop: sends the 'stop' command (saves the world)."""
        self.send("stop")

    def kill(self):
        if self.proc is not None:
            self.proc.kill()
