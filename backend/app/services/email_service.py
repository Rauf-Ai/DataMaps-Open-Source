from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import logging
import hashlib
import re
import time
from email.message import EmailMessage
from html import escape as _esc
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings


settings = get_settings()
log = logging.getLogger("email")
_llm_alert_cache: dict[str, float] = {}

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _render(template: str, **ctx) -> str:
    return _env.get_template(template).render(**ctx)


async def smtp_send(msg: EmailMessage) -> None:
    """Низкоуровневая отправка через настроенный SMTP. Бросает исключение при ошибке.

    Порт 465 → неявный SSL (use_tls); 587/25 → STARTTLS по флагу SMTP_TLS.
    reg.ru и большинство почтовых хостингов используют 465/SSL.
    """
    use_ssl = settings.smtp_port == 465
    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        use_tls=use_ssl,
        start_tls=settings.smtp_tls if not use_ssl else False,
        validate_certs=False,
    )


async def send_email(to: str, subject: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("Откройте письмо в HTML-режиме.")
    msg.add_alternative(html_body, subtype="html")
    try:
        await smtp_send(msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("SMTP send failed to %s: %s", to, exc)


async def send_verification_email(to: str, verify_url: str) -> None:
    html = _render("verify.html", verify_url=verify_url, app_url=settings.app_url)
    await send_email(to, "Подтверждение регистрации — DataMaps", html)


async def send_password_reset_email(to: str, reset_url: str) -> None:
    html = _render("reset.html", reset_url=reset_url, app_url=settings.app_url)
    await send_email(to, "Восстановление пароля — DataMaps", html)


async def send_payment_success_email(to: str, package_name: str, credits: int, amount_rub: float) -> None:
    html = _render(
        "payment_success.html",
        package_name=package_name,
        credits=credits,
        amount_rub=amount_rub,
        app_url=settings.app_url,
    )
    await send_email(to, "Оплата прошла успешно", html)


async def send_admin_alert(subject: str, body_text: str, link: Optional[str] = None) -> None:
    """Уведомление владельцу (ADMIN_EMAIL) — для поддержки, когда Telegram недоступен."""
    to = settings.admin_email
    if not to:
        log.info("send_admin_alert пропущен: ADMIN_EMAIL не задан")
        return
    safe = _esc(body_text).replace("\n", "<br>")
    link_html = (f'<p style="margin-top:16px"><a href="{_esc(link)}">Открыть в админке →</a></p>'
                 if link else "")
    html = (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;'
            f'line-height:1.5;color:#222">'
            f'<p>{safe}</p>{link_html}'
            f'<p style="color:#888;font-size:12px;margin-top:20px">DataMaps · поддержка</p></div>')
    await send_email(to, subject, html)


def _redact_llm_error(error: str) -> str:
    value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", str(error))
    value = re.sub(r"\b(?:sk|or)-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
    return value[:1000]


async def send_llm_failure_alert(error: str, request_id: Optional[str], user_id: int) -> bool:
    """Notify the owner once per error signature in a ten-minute window."""
    safe_error = _redact_llm_error(error)
    signature = hashlib.sha256(safe_error.encode("utf-8")).hexdigest()
    now = time.monotonic()
    last_sent = _llm_alert_cache.get(signature)
    if last_sent is not None and now - last_sent < 600:
        return False
    _llm_alert_cache[signature] = now
    body = (
        "AI-планировщик переключился на резервный разбор.\n"
        f"Ошибка: {safe_error}\n"
        f"Request ID: {request_id or 'нет'}\n"
        f"User ID: {user_id}\n"
        f"Модель: {settings.llm_model}\n"
        f"Провайдер: {settings.llm_base_url.split('/')[2] if '://' in settings.llm_base_url else settings.llm_base_url}"
    )
    await send_admin_alert("DataMaps: сбой LLM-планировщика", body)
    return True


async def send_support_reply_email(to: str, snippet: str, link: str) -> None:
    """Письмо пользователю — ответ оператора поддержки со ссылкой на диалог."""
    safe = _esc(snippet[:600])
    html = (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;'
            f'line-height:1.5;color:#222">'
            f'<p>Оператор поддержки DataMaps ответил на ваше обращение:</p>'
            f'<blockquote style="border-left:3px solid #ddd;margin:8px 0;padding:4px 12px;'
            f'color:#444">{safe}</blockquote>'
            f'<p style="margin-top:16px"><a href="{_esc(link)}">Открыть диалог с поддержкой →</a></p>'
            f'<p style="color:#888;font-size:12px;margin-top:20px">DataMaps · поддержка</p></div>')
    await send_email(to, "Ответ поддержки — DataMaps", html)


async def send_export_ready_email(to: str, row_count: int, download_url: str) -> None:
    html = _render(
        "export_ready.html",
        row_count=row_count,
        download_url=download_url,
        app_url=settings.app_url,
    )
    await send_email(to, "Выгрузка готова", html)
