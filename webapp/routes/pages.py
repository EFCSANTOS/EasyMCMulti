"""Dashboard pages, login/session routes, static assets and health."""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from webapp.services import Services, require_auth
from webapp.auth import SESSION_COOKIE, SESSION_TTL_SEC, check_login, is_https
from webapp.pages.layout import SCRIPTS, STATIC_DIR, _render_dashboard_page

_WEBAPP_DIR = os.path.dirname(os.path.dirname(__file__))


def register(app: FastAPI, s: Services) -> None:

    @app.get("/")
    def root() -> HTMLResponse:
        return _render_dashboard_page("overview")

    @app.get("/login")
    def login() -> HTMLResponse:
        template_path = os.path.join(_WEBAPP_DIR, "templates", "login.html")
        with open(template_path, "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())

    @app.post("/api/login")
    def api_login(payload: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        check_login(request, app.state.login_limiter, username, password, app.state.on_lockout)
        token = app.state.sessions.create(username)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SEC, httponly=True, samesite="strict",
                            secure=is_https(request), path="/")
        return {"username": username}

    @app.post("/api/logout")
    def api_logout(request: Request, response: Response) -> dict[str, Any]:
        app.state.sessions.delete(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"message": "Signed out."}

    @app.get("/api/session")
    def api_session(username: str = Depends(require_auth)) -> dict[str, Any]:
        return {"username": username}

    @app.get("/dashboard")
    def dashboard() -> RedirectResponse:
        return RedirectResponse("/dashboard/overview")

    @app.get("/dashboard/{section}")
    def dashboard_section(section: str) -> HTMLResponse:
        return _render_dashboard_page(section)

    @app.get("/dashboard.css")
    def dashboard_css() -> FileResponse:
        return FileResponse(os.path.join(STATIC_DIR, "css", "dashboard.css"))

    @app.get("/js/{name}")
    def dashboard_script(name: str) -> FileResponse:
        if name not in SCRIPTS:
            raise HTTPException(status_code=404, detail="Not found.")
        return FileResponse(os.path.join(STATIC_DIR, "js", name), media_type="text/javascript")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "ez-server-webapp"}
