"""Envoi d'e-mail automatique via SMTP (pour les rapports de crash).

Les identifiants sont stockes LOCALEMENT (MZ_Server_Data/mail_config.json), jamais dans
le code ni le repo. Sans identifiants, l'envoi auto est simplement desactive (fallback
sur l'ouverture du client mail dans crash_reporter).

Gmail : il faut un "mot de passe d'application" (16 caracteres), pas le mot de passe du
compte. https://myaccount.google.com/apppasswords (2FA requise).
"""
import json
import os
import smtplib
import ssl
from email.message import EmailMessage

from . import paths

_CREDS_FILE = os.path.join(paths.DATA_DIR, "mail_config.json")
DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587


def load_creds():
    try:
        with open(_CREDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_creds(address, app_password, host=DEFAULT_HOST, port=DEFAULT_PORT):
    paths.ensure_dirs()
    with open(_CREDS_FILE, "w", encoding="utf-8") as f:
        json.dump({"address": address, "password": app_password,
                   "host": host, "port": port}, f, indent=2)


def is_configured():
    c = load_creds()
    return bool(c.get("address") and c.get("password"))


def send(recipient, subject, body, attachment_path=None):
    """Envoie un mail. Leve une exception si non configure ou en cas d'echec SMTP."""
    c = load_creds()
    if not (c.get("address") and c.get("password")):
        raise RuntimeError("Envoi auto non configure (aucun identifiant).")

    msg = EmailMessage()
    msg["From"] = c["address"]
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype="text", subtype="plain",
                           filename=os.path.basename(attachment_path))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(c.get("host", DEFAULT_HOST), int(c.get("port", DEFAULT_PORT)),
                      timeout=30) as s:
        s.starttls(context=ctx)
        s.login(c["address"], c["password"])
        s.send_message(msg)
