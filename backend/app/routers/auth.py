from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User, EmailVerification, PasswordReset, PendingRegistration
from app.ratelimit import limiter
from app.schemas.auth import (
    LoginIn,
    MeOut,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RegisterIn,
)
from app.security import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    decode_token,
    generate_token,
    hash_password,
    set_auth_cookies,
    verify_password,
)
from app.services.email_service import send_password_reset_email, send_verification_email
from app.timeutils import utcnow


settings = get_settings()
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _registration_credits() -> int:
    return 0 if settings.local_mode else settings.free_credits_on_register


@router.post("/register", status_code=201)
@limiter.limit("10/minute")
async def register(
    request: Request,
    payload: RegisterIn,
    background: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    email = payload.email.lower()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    smtp_real = bool(settings.smtp_host and settings.smtp_host != "mailhog")
    verification_on = smtp_real and settings.require_email_verification

    # Подтверждённый аккаунт уже существует — повторная регистрация запрещена.
    if existing and existing.email_verified_at:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже зарегистрирован")

    # Подтверждение выключено — создаём/активируем аккаунт сразу.
    if not verification_on:
        if existing:
            existing.password_hash = hash_password(payload.password)
            if existing.email_verified_at is None:
                existing.email_verified_at = datetime.now(timezone.utc)
                existing.credits_balance += _registration_credits()
        else:
            db.add(User(
                email=email,
                password_hash=hash_password(payload.password),
                is_active=True,
                is_admin=False,
                credits_balance=_registration_credits(),
                email_verified_at=datetime.now(timezone.utc),
            ))
        db.commit()
        return {"ok": True, "message": "Регистрация завершена. Войдите в аккаунт."}

    # Подтверждение включено: НЕ создаём User. Кладём заявку в pending_registrations.
    # Повторная регистрация того же email просто перезапишет заявку и пришлёт письмо снова.
    token = generate_token()
    if existing:
        # Легаси: остался неподтверждённый User (создан старой логикой) — обновим пароль и
        # переотправим письмо по старому механизму, чтобы не плодить дубли.
        existing.password_hash = hash_password(payload.password)
        db.flush()
        db.add(EmailVerification(
            token=token, user_id=existing.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
    else:
        db.execute(delete(PendingRegistration).where(PendingRegistration.email == email))
        db.add(PendingRegistration(
            token=token, email=email, password_hash=hash_password(payload.password),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
    db.commit()
    verify_url = f"{settings.app_url}/verify?token={token}"
    background.add_task(send_verification_email, email, verify_url)
    return {"ok": True, "message": "Проверьте почту для подтверждения регистрации"}


@router.get("/verify")
async def verify_email(token: str, response: Response, db: Annotated[Session, Depends(get_db)]):
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    # Новый поток: заявка из pending_registrations — создаём реальный User по клику.
    pr = db.get(PendingRegistration, token)
    if pr:
        if pr.expires_at < now_naive:
            raise HTTPException(status_code=400, detail="Срок действия ссылки истёк")
        user = db.execute(select(User).where(User.email == pr.email)).scalar_one_or_none()
        if user is None:
            user = User(
                email=pr.email,
                password_hash=pr.password_hash,
                is_active=True,
                is_admin=False,
                credits_balance=_registration_credits(),
                email_verified_at=utcnow(),
            )
            db.add(user)
        elif user.email_verified_at is None:
            user.email_verified_at = utcnow()
            user.credits_balance += _registration_credits()
        db.execute(delete(PendingRegistration).where(PendingRegistration.email == pr.email))
        db.commit()
        set_auth_cookies(response, user.id, user.is_admin)
        return {"ok": True}

    # Легаси-поток: токен из email_verifications (для уже созданных неподтверждённых User).
    rec = db.get(EmailVerification, token)
    if not rec or rec.used_at is not None:
        raise HTTPException(status_code=400, detail="Ссылка недействительна")
    if rec.expires_at < now_naive:
        raise HTTPException(status_code=400, detail="Срок действия ссылки истёк")
    user = db.get(User, rec.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
        user.credits_balance += _registration_credits()
    rec.used_at = utcnow()
    db.commit()
    set_auth_cookies(response, user.id, user.is_admin)
    return {"ok": True}


@router.post("/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: LoginIn,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    user = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    if user.is_blocked:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
    set_auth_cookies(response, user.id, user.is_admin)
    return {"ok": True, "is_admin": user.is_admin, "email_verified": user.email_verified_at is not None}


@router.post("/logout")
async def logout(response: Response):
    clear_auth_cookies(response)
    return {"ok": True}


@router.post("/refresh")
async def refresh(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[Optional[str], Cookie(alias=REFRESH_COOKIE)] = None,
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Нет refresh-токена")
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Невалидный refresh-токен")
    user = db.get(User, int(payload["sub"]))
    if not user or user.is_blocked or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь недоступен")
    set_auth_cookies(response, user.id, user.is_admin)
    return {"ok": True}


@router.post("/password-reset/request")
@limiter.limit("5/minute")
async def password_reset_request(
    request: Request,
    payload: PasswordResetRequestIn,
    background: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    user = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()
    if user:
        token = generate_token()
        db.add(
            PasswordReset(
                token=token,
                user_id=user.id,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.commit()
        reset_url = f"{settings.app_url}/reset?token={token}"
        background.add_task(send_password_reset_email, user.email, reset_url)
    return {"ok": True, "message": "Если email зарегистрирован — письмо отправлено"}


@router.post("/password-reset/confirm")
async def password_reset_confirm(
    payload: PasswordResetConfirmIn,
    db: Annotated[Session, Depends(get_db)],
):
    rec = db.get(PasswordReset, payload.token)
    if not rec or rec.used_at is not None:
        raise HTTPException(status_code=400, detail="Ссылка недействительна")
    if rec.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(status_code=400, detail="Срок действия ссылки истёк")
    user = db.get(User, rec.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.password_hash = hash_password(payload.password)
    rec.used_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=MeOut)
async def me(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.services.subscription_service import get_or_create_subscription, quota_snapshot

    sub = get_or_create_subscription(db, user.id)
    db.commit()
    return MeOut(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        email_verified=user.email_verified_at is not None,
        credits_balance=user.credits_balance,
        credits_spent=user.credits_spent,
        total_paid_kop=user.total_paid_kop,
        subscription=quota_snapshot(sub, user),
    )
