"""Friend accounts (FEAT-10): EZ_Server_Data/users.json = {username: {role, salt, hash, created}}.

The owner is always the EZ_WEBAPP_USERNAME/PASSWORD account from the environment, never stored
here, so the owner can't be locked out by editing this file. Passwords are hashed with the stdlib
`hashlib.scrypt` (no compiled dependency, works on Termux).
"""
import hashlib
import hmac
import os
import re
import secrets
import time

from . import fsutil, paths

ROLE_FRIEND = "friend"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def _path():
    return os.path.join(paths.DATA_DIR, "users.json")


def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), **_SCRYPT).hex()


def load():
    data = fsutil.read_json(_path(), {})
    return data if isinstance(data, dict) else {}


def _save(users):
    paths.ensure_dirs()
    fsutil.write_json_atomic(_path(), users)


def new_password():
    """Easy to read out or paste: 12 characters from an unambiguous alphabet."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def set_password(username, password=None):
    """Create the friend (or reset their password). Returns the new password."""
    password = password or new_password()
    users = load()
    salt = secrets.token_hex(16)
    entry = users.get(username, {"role": ROLE_FRIEND, "created": int(time.time())})
    entry.update(salt=salt, hash=_hash(password, salt))
    users[username] = entry
    _save(users)
    return password


def remove(username):
    users = load()
    if users.pop(username, None) is None:
        return False
    _save(users)
    return True


def verify(username, password):
    """The friend's role if the password is right, else None (constant-time comparison)."""
    entry = load().get(username)
    if not entry:
        _hash(password, "00" * 16)  # same work either way, so timing doesn't reveal which names exist
        return None
    if hmac.compare_digest(_hash(password, entry["salt"]), entry["hash"]):
        return entry.get("role", ROLE_FRIEND)
    return None


def listing():
    return [{"username": name, "role": entry.get("role", ROLE_FRIEND), "created": entry.get("created", 0)}
            for name, entry in sorted(load().items())]
