from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

from fastapi import (
    Cookie,
    Depends,
    HTTPException,
    status
)
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import decode_token, ACCESS_COOKIE


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    access_token: Annotated[Optional[str], Cookie(alias=ACCESS_COOKIE)] = None,
) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")
    payload = decode_token(access_token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Невалидный токен")
    user_id = int(payload.get("sub", "0"))
    user = db.get(User, user_id)
    if not user or user.is_blocked or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь недоступен")
    return user


def get_admin_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ только для администраторов")
    return user


def get_optional_user(
    db: Annotated[Session, Depends(get_db)],
    access_token: Annotated[Optional[str], Cookie(alias=ACCESS_COOKIE)] = None,
) -> Optional[User]:
    if not access_token:
        return None
    payload = decode_token(access_token)
    if not payload or payload.get("type") != "access":
        return None
    user = db.get(User, int(payload.get("sub", "0")))
    if not user or user.is_blocked or not user.is_active:
        return None
    return user
