from __future__ import annotations

import base64
import os

from fastapi import HTTPException, status
from fastapi.security import HTTPBasicCredentials


def validate_auth_settings() -> None:
    username = os.getenv("EZ_WEBAPP_USERNAME", "").strip()
    password = os.getenv("EZ_WEBAPP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            "Authentication is required. Set EZ_WEBAPP_USERNAME and EZ_WEBAPP_PASSWORD before starting the web app."
        )


def get_expected_credentials() -> tuple[str, str]:
    username = os.getenv("EZ_WEBAPP_USERNAME", "").strip()
    password = os.getenv("EZ_WEBAPP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            "Authentication is required. Set EZ_WEBAPP_USERNAME and EZ_WEBAPP_PASSWORD before starting the web app."
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


def verify_basic_auth_header(auth_header: str | None) -> str:
    if not auth_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    scheme, _, encoded = auth_header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed.") from None

    username, separator, password = decoded.partition(":")
    if separator != ":":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed.")

    expected_username, expected_password = get_expected_credentials()
    if username != expected_username or password != expected_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return username
