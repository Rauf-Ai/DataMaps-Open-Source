from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import (
    Response
)
from jose import jwt, JWTError
from passlib.context import CryptContext

from app.config import get_settings


settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

ALGORITHM = "HS256"
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def _encode(data: dict[str, Any], expires_delta: timedelta) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    to_encode.update({"iat": now, "exp": now + expires_delta})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(user_id: int, is_admin: bool) -> str:
    return _encode(
        {"sub": str(user_id), "is_admin": is_admin, "type": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: int) -> str:
    return _encode(
        {"sub": str(user_id), "type": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


def set_auth_cookies(response: Response, user_id: int, is_admin: bool) -> None:
    access = create_access_token(user_id, is_admin)
    refresh = create_refresh_token(user_id)
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    if settings.cookie_domain:
        common["domain"] = settings.cookie_domain
    response.set_cookie(ACCESS_COOKIE, access, max_age=settings.access_token_expire_minutes * 60, **common)
    response.set_cookie(REFRESH_COOKIE, refresh, max_age=settings.refresh_token_expire_days * 86400, **common)


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def generate_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)
