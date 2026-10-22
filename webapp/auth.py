from __future__ import annotations

import base64
import os
import secrets

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


# MUST FIX (next commits): this Basic-auth scheme is LAN-grade only. With the PlayIt dashboard
# tunnel (plain HTTP over the internet) the password is sent readable on every request, there is
# no lockout/rate limit for wrong passwords, and the browser keeps it in sessionStorage.
# Replace with login sessions (HttpOnly cookie) + failed-login lockout. See
# docs/knowledge/verification.md "Must fix".
def _credentials_match(username: str, password: str) -> bool:
    """Constant-time comparison so response timing does not leak the expected credentials."""
    expected_username, expected_password = get_expected_credentials()
    username_ok = secrets.compare_digest(username.encode("utf-8"), expected_username.encode("utf-8"))
    password_ok = secrets.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8"))
    return username_ok and password_ok


def verify_credentials(credentials: HTTPBasicCredentials) -> str:
    if not _credentials_match(credentials.username, credentials.password):
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

    if not _credentials_match(username, password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return username
