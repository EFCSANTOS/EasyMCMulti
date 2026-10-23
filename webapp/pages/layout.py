"""Page shell: navigation, top bar, install banner and the sections of each page."""
from __future__ import annotations

import os

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from webapp.pages.sections_main import _SECTION_ADVANCED  # noqa: F401
from webapp.pages.sections_main import _SECTION_ALERTS  # noqa: F401
from webapp.pages.sections_main import _SECTION_ALWAYS_ON  # noqa: F401
from webapp.pages.sections_main import _SECTION_CONSOLE_PREVIEW  # noqa: F401
from webapp.pages.sections_main import _SECTION_METRICS  # noqa: F401
from webapp.pages.sections_main import _SECTION_OVERVIEW_TILES  # noqa: F401
from webapp.pages.sections_main import _SECTION_MIGRATE  # noqa: F401
from webapp.pages.sections_main import _SECTION_PERFORMANCE  # noqa: F401
from webapp.pages.sections_main import _SECTION_SCHEDULE  # noqa: F401
from webapp.pages.sections_main import _SECTION_SETUP  # noqa: F401
from webapp.pages.sections_main import _SECTION_UPDATES  # noqa: F401
from webapp.pages.sections_more import _SECTION_BACKUPS  # noqa: F401
from webapp.pages.sections_more import _SECTION_CONSOLE_FULL  # noqa: F401
from webapp.pages.sections_more import _SECTION_MODS  # noqa: F401
from webapp.pages.sections_more import _SECTION_REMOTE  # noqa: F401
from webapp.pages.sections_more import _SECTION_WORLDS  # noqa: F401
from webapp.pages.sections_diagnosis import _SECTION_DIAGNOSIS, _SECTION_LOGS
from webapp.pages.sections_identity import _SECTION_ALL_PROPERTIES, _SECTION_IDENTITY
from webapp.pages.sections_invite import _SECTION_INVITE
from webapp.pages.sections_players import _SECTION_PLAYERS
from webapp.pages.widgets import _icon

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
# Dashboard scripts in load order: plain scripts sharing one global scope (no build step).
SCRIPTS = ("api.js", "server.js", "content.js", "players.js", "settings.js", "console.js", "identity.js",
           "main.js")
# Changes whenever the CSS/JS change (e.g. after an update) so browsers don't keep stale copies.
ASSET_VERSION = str(int(max(
    [os.path.getmtime(os.path.join(STATIC_DIR, "css", "dashboard.css"))]
    + [os.path.getmtime(os.path.join(STATIC_DIR, "js", name)) for name in SCRIPTS]
)))
_SCRIPT_TAGS = "\n    ".join(f'<script src="/js/{name}?v={ASSET_VERSION}"></script>' for name in SCRIPTS)

_DASHBOARD_NAVIGATION = (
    ("overview", "Overview", "Overview", "Your server at a glance."),
    ("setup", "Server", "Server setup", "Choose the loader, version and resources, then create the server."),
    ("advanced", "Settings", "World settings", "Gameplay and world options written to server.properties."),
    ("worlds", "Worlds", "Worlds", "Upload, switch between and manage your worlds."),
    ("players", "Players", "Players", "Choose who can join: whitelist, operators and bans."),
    ("mods", "Mods", "Mods & plugins", "Find mods on Modrinth or upload your own .jar files."),
    ("remote", "Remote access", "Remote access", "Let friends outside your network join."),
    ("backups", "Backups", "Backups", "Snapshot the world and roll back when needed."),
    ("console", "Console", "Console", "Live server output, commands and admin shortcuts."),
)

# Shown on every page while an install runs (installs continue in the background).
_INSTALL_BANNER = """
        <div id="install-banner" class="card install-banner" hidden>
          <div class="install-head">
            <strong>Installing server</strong>
            <span data-field="install_message" class="muted"></span>
          </div>
          <div class="progress"><div id="install-progress-bar" class="progress-bar"></div></div>
        </div>
"""

_TOPBAR_CONTROLS = f"""
          <div class="topbar-actions">
            <span id="server-state" class="state-pill" data-state="unknown" role="status">
              <span class="state-dot" aria-hidden="true"></span><span id="server-state-label">Loading…</span>
            </span>
            <div class="btn-group" role="group" aria-label="Server controls">
              <button id="start-button" class="btn btn-primary btn-sm" type="button" title="Start server" disabled>{_icon("play")} Start</button>
              <button id="restart-button" class="btn btn-ghost btn-sm" type="button" title="Restart server" disabled>{_icon("restart")} Restart</button>
              <button id="stop-button" class="btn btn-ghost btn-sm" type="button" title="Stop server" disabled>{_icon("stop")} Stop</button>
            </div>
          </div>
"""


_DASHBOARD_PAGES: dict[str, tuple[str, ...]] = {
    "overview": (_SECTION_OVERVIEW_TILES, _SECTION_DIAGNOSIS, _SECTION_INVITE, _SECTION_METRICS, _SECTION_CONSOLE_PREVIEW),
    "setup": (_SECTION_SETUP, _SECTION_MIGRATE, _SECTION_PERFORMANCE, _SECTION_ALWAYS_ON, _SECTION_SCHEDULE, _SECTION_ALERTS,
              _SECTION_UPDATES),
    "advanced": (_SECTION_ADVANCED, _SECTION_IDENTITY, _SECTION_ALL_PROPERTIES),
    "worlds": (_SECTION_WORLDS,),
    "players": (_SECTION_PLAYERS,),
    "mods": (_SECTION_MODS,),
    "remote": (_SECTION_REMOTE, _SECTION_INVITE),
    "backups": (_SECTION_BACKUPS,),
    "console": (_SECTION_DIAGNOSIS, _SECTION_CONSOLE_FULL, _SECTION_LOGS),
}


def _render_dashboard_page(section: str) -> HTMLResponse:
    normalized = section.strip("/").lower()
    if normalized not in _DASHBOARD_PAGES:
        raise HTTPException(status_code=404, detail=f"Dashboard section '{section}' not found.")

    nav_links = []
    for key, label, _title, _sub in _DASHBOARD_NAVIGATION:
        current = ' active" aria-current="page' if key == normalized else ""
        nav_links.append(f'<a href="/dashboard/{key}" class="nav-link{current}">{_icon(key)}<span>{label}</span></a>')
    nav_html = "".join(nav_links)
    _key, _label, title, subtitle = next(item for item in _DASHBOARD_NAVIGATION if item[0] == normalized)
    body_html = "".join(_DASHBOARD_PAGES[normalized])

    return HTMLResponse(
        f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title} · EZ Minecraft Server</title>
    <link rel="stylesheet" href="/dashboard.css?v={ASSET_VERSION}" />
  </head>
  <body data-page="{normalized}">
    <div class="app-shell">
      <aside class="sidebar">
        <a class="brand" href="/dashboard/overview">
          <span class="brand-logo" aria-hidden="true"></span>
          <span>
            <span class="brand-title">EZ Minecraft</span>
            <span class="brand-sub">Server Dashboard</span>
          </span>
        </a>

        <nav class="nav" aria-label="Dashboard sections">{nav_html}</nav>

        <div class="sidebar-footer">
          <span class="session" id="session-status">Signed in</span>
          <button id="logout-button" class="icon-btn" type="button" title="Sign out" aria-label="Sign out">{_icon("logout")}</button>
        </div>
      </aside>

      <div class="main">
        <header class="topbar">
          <div>
            <h1 class="page-title">{title}</h1>
            <p class="page-sub">{subtitle}</p>
          </div>
          {_TOPBAR_CONTROLS}
        </header>
        <main class="content">
          {_INSTALL_BANNER}
          {body_html}
        </main>
      </div>
    </div>
    <div id="action-status" class="toast" role="status" aria-live="polite" hidden></div>
    {_SCRIPT_TAGS}
  </body>
</html>
        """
    )
