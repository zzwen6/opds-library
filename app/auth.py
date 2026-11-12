from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

security = HTTPBasic(realm="OPDS Library", auto_error=False)


def require_basic(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    settings = get_settings()
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username, settings.auth_username)
        and secrets.compare_digest(credentials.password, settings.auth_password)
    )
    if not ok:
        headers: dict[str, str] = {}
        # 网页用 JS 带凭证加载封面；401 若带 WWW-Authenticate，浏览器会再弹登录框。
        # OPDS 阅读器需要这个头才会提示登录，所以只给非 /api 的路径加上。
        if not request.url.path.startswith("/api"):
            headers["WWW-Authenticate"] = 'Basic realm="OPDS Library"'
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权",
            headers=headers,
        )
    return credentials.username
