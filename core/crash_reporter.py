"""Rapport de crash automatique.

En cas d'exception non geree :
1. construit un rapport detaille et structure (facile a relire pour debugger),
2. l'enregistre dans MZ_Server_Data/crash_reports/,
3. ouvre le client mail par defaut pre-rempli vers DEV_EMAIL avec le rapport.

Pas d'envoi SMTP (pas de mot de passe a stocker) : on passe par mailto:.
"""
import datetime
import os
import platform
import sys
import traceback
import urllib.parse
import webbrowser

from . import paths

DEV_EMAIL = "matheozimmer@gmail.com"


def build_report(context, exc_info, version):
    """Construit le texte du rapport. context = dict d'etat, exc_info = (type, exc, tb)."""
    exc_type, exc, tb = exc_info
    lines = []
    add = lines.append

    add("==== MZ MINECRAFT LAUNCHER - RAPPORT DE CRASH ====")
    add(f"Date          : {datetime.datetime.now().isoformat(timespec='seconds')}")
    add(f"Version app   : {version}")
    add("")
    add("--- SYSTEME ---")
    add(f"OS            : {platform.platform()}")
    add(f"Python        : {platform.python_version()} ({platform.architecture()[0]})")
    add(f"Gele (.exe)   : {getattr(sys, 'frozen', False)}")
    add("")
    add("--- ETAT DU LAUNCHER ---")
    for k, v in context.items():
        if k.startswith("_"):  # cles techniques (ex _console_tail) traitees a part
            continue
        add(f"{k:<14}: {v}")
    add("")
    add("--- DERNIERES LIGNES DE CONSOLE ---")
    for line in context.get("_console_tail", []):
        add(line)
    add("")
    add("--- TRACEBACK ---")
    add("".join(traceback.format_exception(exc_type, exc, tb)).rstrip())
    add("")
    add("==== FIN DU RAPPORT ====")
    return "\n".join(lines)


def save_report(text):
    """Ecrit le rapport dans un fichier horodate et renvoie son chemin."""
    d = os.path.join(paths.DATA_DIR, "crash_reports")
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(d, f"crash_{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def open_mail(text, file_path):
    """Ouvre le client mail pre-rempli (corps tronque si tres long)."""
    subject = "MZ Launcher - Rapport de crash"
    body = text
    if len(body) > 1800:  # certains clients tronquent les mailto: trop longs
        body = body[:1800] + (
            f"\n\n[...] Rapport complet en piece jointe :\n{file_path}")
    url = "mailto:{to}?subject={s}&body={b}".format(
        to=DEV_EMAIL,
        s=urllib.parse.quote(subject),
        b=urllib.parse.quote(body))
    try:
        webbrowser.open(url)
    except OSError:
        pass


def deliver(text, file_path):
    """Envoie le rapport : SMTP auto si configure, sinon ouverture du client mail.

    Renvoie "auto" (envoye tout seul) ou "manual" (mail ouvert, l'utilisateur clique).
    """
    try:
        from . import mailer
        if mailer.is_configured():
            mailer.send(DEV_EMAIL, "MZ Launcher - Rapport de crash", text, file_path)
            return "auto"
    except Exception:
        pass  # echec SMTP -> on retombe sur le mode manuel
    open_mail(text, file_path)
    return "manual"


def handle(context, exc_info, version):
    """Pipeline complet : build -> save -> envoi. Renvoie (chemin, texte, mode)."""
    text = build_report(context, exc_info, version)
    path = save_report(text)
    mode = deliver(text, path)
    return path, text, mode


def install(get_context, version, on_report=None):
    """Branche les hooks d'exception (GUI + threads + global).

    get_context() -> dict d'etat courant. on_report(path, text) appele apres coup (UI).
    Renvoie la fonction handler (a brancher aussi sur root.report_callback_exception).
    """
    def _handler(exc_type, exc, tb):
        try:
            path, _text, mode = handle(get_context(), (exc_type, exc, tb), version)
            if on_report:
                on_report(path, mode)
        except Exception:
            traceback.print_exception(exc_type, exc, tb)  # dernier recours

    sys.excepthook = lambda et, e, tb: _handler(et, e, tb)
    try:  # crashs dans les threads de fond (Python 3.8+)
        import threading
        threading.excepthook = lambda a: _handler(a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:
        pass
    return _handler
