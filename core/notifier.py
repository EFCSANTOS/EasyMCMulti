"""Alerts: email through SMTP (e.g. Gmail with an app password) and an uptime heartbeat.

Email covers what the app itself can see (crashes, new dashboard link, tunnel problems).
The heartbeat covers what it cannot: if the phone dies, loses internet or the app is killed,
the pings to the heartbeat URL stop and the external service (e.g. healthchecks.io, free)
emails the user.

Settings live in EZ_Server_Data/notify_config.json. The SMTP password is stored there in plain
text (like the PlayIt secret) and is never returned by the API.
"""
import json
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests

from . import paths

_CONFIG_FILE = os.path.join(paths.DATA_DIR, "notify_config.json")

DEFAULTS = {
    "email_enabled": False,
    "email_to": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    # Which alert categories are emailed.
    "notify_server": True,
    "notify_link": True,
    "notify_tunnels": True,
    "notify_updates": True,
    "notify_security": True,
    "heartbeat_url": "",
}
PUBLIC_KEYS = [key for key in DEFAULTS if key != "smtp_password"]


def load_config():
    config = dict(DEFAULTS)
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            config.update(stored)
    except (OSError, ValueError):
        pass
    return config


def save_config(update):
    """Merge `update` into the saved settings. An empty smtp_password keeps the saved one."""
    config = load_config()
    for key, value in update.items():
        if key not in DEFAULTS and key != "last_link":
            continue
        if key == "smtp_password" and not value:
            continue
        config[key] = value
    config["smtp_port"] = int(config.get("smtp_port") or 587)
    paths.ensure_dirs()
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return config


def public_config():
    """Settings safe to show in the browser (no password)."""
    config = load_config()
    data = {key: config[key] for key in PUBLIC_KEYS}
    data["password_set"] = bool(config.get("smtp_password"))
    return data


def email_ready(config):
    return bool(config.get("email_enabled") and config.get("email_to") and config.get("smtp_host")
                and config.get("smtp_user") and config.get("smtp_password"))


def send_email(config, subject, body):
    """Send one email. Raises on failure (bad password, blocked port, ...)."""
    msg = EmailMessage()
    msg["From"] = config["smtp_user"]
    msg["To"] = config["email_to"]
    msg["Subject"] = subject
    msg.set_content(body)
    host = config["smtp_host"]
    port = int(config.get("smtp_port") or 587)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
            smtp.login(config["smtp_user"], config["smtp_password"])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(config["smtp_user"], config["smtp_password"])
            smtp.send_message(msg)


def ping_heartbeat(url):
    """One check-in to the heartbeat URL. Returns True on success."""
    try:
        return requests.get(url, timeout=10).ok
    except requests.RequestException:
        return False
