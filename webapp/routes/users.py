"""Friend accounts, managed by the owner (FEAT-10)."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from core import users
from webapp import auth, common
from webapp.services import Services, require_auth


def register(app: FastAPI, s: Services) -> None:
    def payload(message: str = "", **extra: Any) -> dict[str, Any]:
        return {"users": users.listing(), "friends_can_stop": bool(common._effective_config().get("friends_can_stop")),
                "message": message, **extra}

    def checked_name(value: Any) -> str:
        name = str(value or "").strip()
        if not users.USERNAME_PATTERN.match(name):
            raise HTTPException(status_code=400, detail="Usernames are 2 to 32 letters, digits, dots, dashes or underscores.")
        owner_name, _ = auth.get_expected_credentials()
        if name.lower() == owner_name.lower():
            raise HTTPException(status_code=400, detail="That is the owner's username.")
        return name

    @app.get("/api/users")
    def list_users(_: str = Depends(require_auth)) -> dict[str, Any]:
        return payload()

    @app.post("/api/users")
    def add_user(body: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
        name = checked_name(body.get("username"))
        if any(entry["username"].lower() == name.lower() for entry in users.listing()):
            raise HTTPException(status_code=409, detail=f"{name} already has an account.")
        password = users.set_password(name)
        s.append_console_line(f"[dashboard] friend account {name} created")
        return payload(f"Account created. Send {name} the dashboard link and this password.", username=name,
                       password=password)

    @app.post("/api/users/{name}/reset")
    def reset_user(name: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        if name not in {entry["username"] for entry in users.listing()}:
            raise HTTPException(status_code=404, detail="No such account.")
        password = users.set_password(name)
        app.state.sessions.delete_user(name)
        return payload(f"New password for {name}; they were signed out.", username=name, password=password)

    @app.delete("/api/users/{name}")
    def remove_user(name: str, _: str = Depends(require_auth)) -> dict[str, Any]:
        if not users.remove(name):
            raise HTTPException(status_code=404, detail="No such account.")
        app.state.sessions.delete_user(name)
        s.append_console_line(f"[dashboard] friend account {name} removed")
        return payload(f"{name} can no longer sign in.")
