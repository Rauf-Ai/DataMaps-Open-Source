from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""CLI-утилиты.

Использование:
    python -m app.cli create-admin admin@example.com --password Pa$$w0rd
    python -m app.cli set-credits admin@example.com --delta 1000
"""

import secrets
import sys

import typer
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password
from app.timeutils import utcnow


cli = typer.Typer(no_args_is_help=True)


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


@cli.command("create-admin")
def create_admin(email: str, password: str = typer.Option(None, "--password", "-p")):
    """Создаёт администратора. Если пароль не задан — генерируется случайный."""
    _ensure_schema()
    pwd = password or secrets.token_urlsafe(12)
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
        from datetime import datetime
        if user:
            user.is_admin = True
            user.is_active = True
            user.is_blocked = False
            if password:
                user.password_hash = hash_password(pwd)
            db.commit()
            typer.echo(f"✓ Существующий пользователь {email} повышен до admin")
        else:
            user = User(
                email=email.lower(),
                password_hash=hash_password(pwd),
                is_active=True,
                is_admin=True,
                email_verified_at=utcnow(),
                credits_balance=0,
            )
            db.add(user)
            db.commit()
            typer.echo(f"✓ Создан admin {email}")
        if not password:
            typer.echo(f"  Пароль: {pwd}")
            typer.echo("  Сохраните его — он больше не будет показан.")
    finally:
        db.close()


@cli.command("verify-email")
def verify_email(email: str):
    """Вручную подтверждает email пользователя и начисляет бесплатные кредиты."""
    _ensure_schema()
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
        if not user:
            typer.echo(f"Пользователь {email} не найден", err=True)
            sys.exit(1)
        if user.email_verified_at:
            typer.echo(f"Email {email} уже подтверждён")
            return
        user.email_verified_at = utcnow()
        from app.config import get_settings
        user.credits_balance += get_settings().free_credits_on_register
        db.commit()
        typer.echo(f"✓ Email {email} подтверждён, начислено {get_settings().free_credits_on_register} кредитов")
    finally:
        db.close()


@cli.command("verify-pending")
def verify_pending():
    """Подтверждает email ВСЕМ неподтверждённым пользователям и начисляет им
    приветственные кредиты. Нужно один раз после отключения SMTP, чтобы
    «расстрять» тех, кто зарегистрировался, но не получил письмо."""
    _ensure_schema()
    from app.config import get_settings
    bonus = get_settings().free_credits_on_register
    db = SessionLocal()
    try:
        users = db.execute(select(User).where(User.email_verified_at.is_(None))).scalars().all()
        for u in users:
            u.email_verified_at = utcnow()
            u.credits_balance += bonus
        db.commit()
        typer.echo(f"✓ Подтверждено пользователей: {len(users)}, начислено по {bonus} кр.")
    finally:
        db.close()


@cli.command("send-test-email")
def send_test_email(to: str):
    """Тестовая отправка через текущие SMTP-настройки — проверка доставки.
    В отличие от обычной отправки, показывает реальную ошибку SMTP."""
    import asyncio
    from email.message import EmailMessage

    from app.config import get_settings
    from app.services.email_service import smtp_send

    s = get_settings()
    typer.echo(f"SMTP host={s.smtp_host} port={s.smtp_port} tls={s.smtp_tls} "
               f"user={s.smtp_user or '—'} from={s.email_from}")
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = to
    msg["Subject"] = "Тест доставки — DataMaps"
    msg.set_content("Тестовое письмо DataMaps. Если вы его видите — SMTP работает.")
    msg.add_alternative(
        "<p>Тестовое письмо <b>DataMaps</b>. Если видите — SMTP работает.</p>", subtype="html")
    try:
        asyncio.run(smtp_send(msg))
        typer.echo(f"✓ Отправлено на {to}. Проверьте «Входящие» и «Спам».")
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"✗ Ошибка отправки: {exc}", err=True)
        sys.exit(1)


@cli.command("set-credits")
def set_credits(email: str, delta: int = typer.Option(..., "--delta")):
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
        if not user:
            typer.echo(f"Пользователь {email} не найден", err=True)
            sys.exit(1)
        user.credits_balance = max(0, user.credits_balance + delta)
        db.commit()
        typer.echo(f"✓ {email}: баланс = {user.credits_balance}")
    finally:
        db.close()


@cli.command("google-test")
def google_test(query: str, shot: str = typer.Option("/data/google_test.png", "--shot")):
    """Проверка доступности Google Карт с этого сервера (гейт перед Google-парсером).

    Открывает поиск Google Maps в headless-Chromium, обходит consent, детектит
    капчу/блокировку и считает карточки результатов. Печатает вердикт PASS/BLOCKED
    и сохраняет скриншот (по умолчанию /data/google_test.png).
    """
    from urllib.parse import quote

    from app.services.maps.browser import _CONTEXT_OPTS, _launch_kwargs

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        typer.echo("✗ Playwright не установлен", err=True)
        sys.exit(1)

    url = f"https://www.google.com/maps/search/{quote(query)}/?hl=ru"
    typer.echo(f"→ Открываю: {url}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**_launch_kwargs())
        ctx = browser.new_context(**_CONTEXT_OPTS)
        # Пытаемся обойти consent-wall заранее выставленной cookie
        try:
            ctx.add_cookies([
                {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
                {"name": "SOCS", "value": "CAI", "domain": ".google.com", "path": "/"},
            ])
        except Exception:  # noqa: BLE001
            pass
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            typer.echo("✗ BLOCKED: таймаут загрузки (сеть/блокировка)")
            browser.close()
            sys.exit(2)

        page.wait_for_timeout(2_500)

        # Пробуем принять consent, если он показался
        for sel in ('button[aria-label*="Accept all"]', 'button[aria-label*="Принять все"]',
                    'form[action*="consent"] button', 'button:has-text("Принять все")',
                    'button:has-text("Accept all")'):
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click(timeout=3_000)
                    page.wait_for_timeout(2_500)
                    break
            except Exception:  # noqa: BLE001
                pass

        final_url = page.url
        html = ""
        try:
            html = page.content()
        except Exception:  # noqa: BLE001
            pass

        # Детект блокировок
        blocked_markers = ("consent.google.com" in final_url or "/sorry/" in final_url
                           or "recaptcha" in html.lower() or "unusual traffic" in html.lower())

        # Считаем карточки результатов
        feed = page.query_selector('div[role="feed"]')
        cards = page.query_selector_all('a[href*="/maps/place/"]')
        n_cards = len(cards)

        try:
            page.screenshot(path=shot, full_page=False)
            shot_msg = f"скриншот: {shot}"
        except Exception as exc:  # noqa: BLE001
            shot_msg = f"скриншот не сохранён ({exc})"

        browser.close()

        typer.echo(f"  финальный URL: {final_url}")
        typer.echo(f"  лента результатов (role=feed): {'да' if feed else 'нет'}")
        typer.echo(f"  карточек мест на странице: {n_cards}")
        typer.echo(f"  {shot_msg}")

        if blocked_markers or (not feed and n_cards == 0):
            typer.echo("\n✗ BLOCKED — Google отдал consent/капчу или пустую страницу. "
                       "Скрапинг с этого сервера не пойдёт без прокси.")
            sys.exit(2)
        typer.echo(f"\n✓ PASS — Google отдаёт результаты ({n_cards} карточек). "
                   "Можно доделывать Google-источник.")


if __name__ == "__main__":
    cli()
