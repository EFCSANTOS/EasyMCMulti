"""Self-update from the git repository the app runs from.

The app is installed as a git clone (termux-setup.sh clones it; Windows users clone it), so an
update is: fetch, fast-forward the current branch (`git pull --ff-only`), reinstall Python packages
if requirements.txt changed, then restart the dashboard process on the new code.

Safety rules: never touch local edits (refuse instead), never merge or rebase (fast-forward only),
and roll the code back if the package install fails.
"""
import os
import shutil
import subprocess
import sys

from . import hostenv, paths

REPO_DIR = paths.base_dir()
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _git(*args, timeout=60, strip=True):
    # core.fileMode=false: older termux-setup.sh ran `chmod +x` on run.sh/ezmc.sh, and a
    # permission-only change must not count as a local edit that blocks updating.
    result = subprocess.run(
        ["git", "-C", REPO_DIR, "-c", "core.fileMode=false", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip() if strip else result.stdout


def unavailable_reason():
    """Why updates can't work here, or None when they can."""
    if shutil.which("git") is None:
        return "git is not installed (Termux: pkg install git)."
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return "This copy of the app is not a git clone, so it cannot update itself."
    return None


def _commit_lines(output):
    commits = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"hash": parts[0], "subject": parts[1], "when": parts[2]})
    return commits


def status(fetch=True):
    """Compare the running checkout with its upstream branch.

    Returns {available, reason, branch, upstream, current, behind, ahead, commits, local_changes}.
    """
    reason = unavailable_reason()
    if reason:
        return {"available": False, "reason": reason}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    try:
        upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except RuntimeError:
        upstream = f"origin/{branch}"
    if fetch:
        _git("fetch", "--quiet", upstream.split("/", 1)[0], timeout=120)
    current = _commit_lines(_git("log", "-1", "--format=%h%x09%s%x09%cr"))
    behind = int(_git("rev-list", "--count", f"HEAD..{upstream}"))
    ahead = int(_git("rev-list", "--count", f"{upstream}..HEAD"))
    commits = _commit_lines(_git("log", "-n", "20", "--format=%h%x09%s%x09%cr", f"HEAD..{upstream}"))
    # Porcelain lines are "XY path"; the leading status column may be a space, so don't strip.
    local_changes = [line for line in _git("status", "--porcelain", "--untracked-files=no", strip=False).splitlines() if line]
    return {
        "available": True,
        "reason": "",
        "branch": branch,
        "upstream": upstream,
        "current": current[0] if current else None,
        "latest_hash": _git("rev-parse", "--short", upstream),
        "behind": behind,
        "ahead": ahead,
        "commits": commits,
        "local_changes": [line[3:] for line in local_changes],
    }


def install_requirements(log):
    """pip install -r requirements.txt with the running interpreter (Termux: without psutil)."""
    with open(os.path.join(REPO_DIR, "requirements.txt"), "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    env = os.environ.copy()
    if hostenv.is_termux():
        lines = [line for line in lines if not line.startswith("psutil")]
        try:
            api = subprocess.run(["getprop", "ro.build.version.sdk"], capture_output=True, text=True).stdout.strip()
            if api:
                env["ANDROID_API_LEVEL"] = api  # needed to compile pydantic-core on Android
        except OSError:
            pass
    paths.ensure_dirs()
    requirements = os.path.join(paths.DATA_DIR, "update-requirements.txt")
    with open(requirements, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("Installing updated Python packages (on a phone this can take several minutes)...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", requirements], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=3600, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
        raise RuntimeError(f"pip install failed:\n{tail}")


def apply(log):
    """Fast-forward to the upstream branch. Returns {updated, from, to, changed_files}."""
    state = status(fetch=True)
    if not state["available"]:
        raise RuntimeError(state["reason"])
    if state["local_changes"]:
        files = ", ".join(state["local_changes"][:5])
        raise RuntimeError(f"Local changes would be overwritten ({files}). Commit or discard them first.")
    if state["ahead"]:
        raise RuntimeError(f"This copy has {state['ahead']} commit(s) that are not on {state['upstream']}; "
                           "update it manually with git.")
    if not state["behind"]:
        return {"updated": False}

    old = _git("rev-parse", "HEAD")
    log(f"Updating {state['branch']}: {state['behind']} new commit(s) from {state['upstream']}...")
    _git("merge", "--ff-only", state["upstream"], timeout=300)
    changed = _git("diff", "--name-only", old, "HEAD").splitlines()
    if "requirements.txt" in changed:
        try:
            install_requirements(log)
        except Exception:
            _git("reset", "--hard", old)  # only undoes the commits just pulled: there were no local edits
            log("Package install failed; the update was rolled back.")
            raise
    return {"updated": True, "from": old[:7], "to": _git("rev-parse", "--short", "HEAD"), "changed_files": changed}


def restart_process():
    """Replace the running dashboard with a fresh process on the new code. Does not return.

    POSIX: exec in place (same PID, so termux-services/runit keeps supervising it; listening
    sockets are non-inheritable and close on exec). Windows: exec would race the old process for
    the port, so a small helper waits for it to exit and then relaunches the same command.
    """
    command = [sys.executable, *sys.orig_argv[1:]]
    os.chdir(REPO_DIR)
    if os.name == "nt":
        relaunch = "import subprocess, sys, time; time.sleep(3); sys.exit(subprocess.call(sys.argv[1:]))"
        subprocess.Popen([sys.executable, "-c", relaunch, *command])
        os._exit(0)
    os.execv(sys.executable, command)
