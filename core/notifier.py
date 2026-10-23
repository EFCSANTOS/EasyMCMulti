"""Alerts: email through SMTP (e.g. Gmail with an app password), Discord webhooks and an uptime heartbeat.

Email covers what the app itself can see (crashes, new dashboard link, tunnel problems).
The heartbeat covers what it cannot: if the phone dies, loses internet or the app is killed,
the pings to the heartbeat URL stop and the external service (e.g. healthchecks.io, free)
emails the user.

Discord (FEAT-05) has two webhooks: the owner channel gets the same alerts as email; the friends
channel only gets online/offline, joins/leaves and the status page link (never the dashboard link).

Settings live in EZ_Server_Data/notify_config.json. The SMTP password and webhook URLs are stored
there in plain text (like the PlayIt secret) and are never returned by the API.
"""
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage

import requests

from . import fsutil, paths

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
    "notify_backup": True,
    # Device health: high CPU/RAM use and high temperature, each sent by email, Discord or both.
    "notify_usage": True,
    "notify_temperature": True,
    "channels_usage": "both",
    "channels_temperature": "both",
    "alert_cpu_percent": 90,
    "alert_ram_percent": 90,
    "alert_usage_minutes": 5,
    "alert_temp_c": 80,
    "alert_battery_c": 45,
    "heartbeat_url": "",
    # Discord webhooks (secret URLs) and what the friends channel gets.
    "discord_owner_url": "",
    "discord_friends_url": "",
    "friends_notify_status": True,
    "friends_notify_players": True,
    "friends_notify_link": True,
}
# Write-only: an empty value keeps the saved one, null removes it; the API only says whether it is set.
SECRET_KEYS = ("smtp_password", "discord_owner_url", "discord_friends_url")
PUBLIC_KEYS = [key for key in DEFAULTS if key not in SECRET_KEYS]
DISCORD_WEBHOOK = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")
DISCORD_MAX_LENGTH = 2000
CHANNELS = ("both", "email", "discord")
# Limits the dashboard accepts: key -> (lowest, highest).
LIMITS = {
    "alert_cpu_percent": (10, 100),
    "alert_ram_percent": (10, 100),
    "alert_usage_minutes": (1, 120),
    "alert_temp_c": (30, 120),
    "alert_battery_c": (25, 80),
}


def load_config():
    config = dict(DEFAULTS)
    stored = fsutil.read_json(_CONFIG_FILE, {})
    if isinstance(stored, dict):
        config.update(stored)
    return config


def save_config(update):
    """Merge `update` into the saved settings.

    Secrets (SECRET_KEYS): empty keeps the saved value, None removes it. Raises ValueError for a
    webhook URL that isn't a Discord webhook.
    """
    config = load_config()
    for key, value in update.items():
        if key not in DEFAULTS and key != "last_link":
            continue
        if key in SECRET_KEYS:
            if value is None:
                config[key] = ""
                continue
            if not value:
                continue
            if key.startswith("discord_") and not DISCORD_WEBHOOK.match(str(value).strip()):
                raise ValueError("That is not a Discord webhook URL (Server settings → Integrations → Webhooks → Copy URL).")
            value = str(value).strip()
        elif key.startswith("channels_") and value not in CHANNELS:
            raise ValueError("Choose where alerts go: email, Discord or both.")
        elif key in LIMITS:
            low, high = LIMITS[key]
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError("Alert limits must be whole numbers.") from None
            if not low <= value <= high:
                raise ValueError(f"That alert limit must be between {low} and {high}.")
        config[key] = value
    config["smtp_port"] = int(config.get("smtp_port") or 587)
    paths.ensure_dirs()
    fsutil.write_json_atomic(_CONFIG_FILE, config)
    return config


def public_config():
    """Settings safe to show in the browser (no password)."""
    config = load_config()
    data = {key: config[key] for key in PUBLIC_KEYS}
    data["password_set"] = bool(config.get("smtp_password"))
    data["discord_owner_set"] = bool(config.get("discord_owner_url"))
    data["discord_friends_set"] = bool(config.get("discord_friends_url"))
    return data


def channels(config, category):
    """(email, discord) allowed for `category`: "both" unless the category has its own choice."""
    choice = config.get(f"channels_{category}", "both")
    return choice in ("both", "email"), choice in ("both", "discord")


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


def send_discord(url, content):
    """Post one message to a Discord webhook. Waits once on a 429 (rate limit). Raises on failure."""
    body = {"content": content[:DISCORD_MAX_LENGTH], "allowed_mentions": {"parse": []}}  # no @everyone from names
    for attempt in range(2):
        response = requests.post(url, json=body, timeout=15)
        if response.status_code == 429 and attempt == 0:
            try:
                wait = float(response.json().get("retry_after", 1))
            except ValueError:
                wait = 1.0
            time.sleep(min(wait, 10.0))
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"Discord answered HTTP {response.status_code}")
        return
    raise RuntimeError("Discord rate limit: try again in a moment")


def ping_heartbeat(url):
    """One check-in to the heartbeat URL. Returns True on success."""
    try:
        return requests.get(url, timeout=10).ok
    except requests.RequestException:
        return False
