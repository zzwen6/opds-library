from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

security = HTTPBasic(realm="OPDS Library")


def require_basic(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    settings = get_settings()
    user_ok = secrets.compare_digest(credentials.username, settings.auth_username)
    pass_ok = secrets.compare_digest(credentials.password, settings.auth_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权",
            headers={"WWW-Authenticate": "Basic realm=\"OPDS Library\""},
        )
    return credentials.username
