from __future__ import annotations

import os

from fastapi import HTTPException, status
from fastapi.security import HTTPBasicCredentials


def validate_auth_settings() -> None:
    username = os.getenv("MZ_WEBAPP_USERNAME", "").strip()
    password = os.getenv("MZ_WEBAPP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            "Authentication is required. Set MZ_WEBAPP_USERNAME and MZ_WEBAPP_PASSWORD before starting the web app."
        )


def get_expected_credentials() -> tuple[str, str]:
    username = os.getenv("MZ_WEBAPP_USERNAME", "").strip()
    password = os.getenv("MZ_WEBAPP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            "Authentication is required. Set MZ_WEBAPP_USERNAME and MZ_WEBAPP_PASSWORD before starting the web app."
        )
    return username, password


def verify_credentials(credentials: HTTPBasicCredentials) -> str:
    expected_username, expected_password = get_expected_credentials()
    if credentials.username != expected_username or credentials.password != expected_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
