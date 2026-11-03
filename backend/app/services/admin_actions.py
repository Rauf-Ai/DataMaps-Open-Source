from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Ядро admin-операций над пользователями (начисление кредитов, блокировка).

Вынесено из routers/admin.py, чтобы один и тот же код вызывали:
  * admin-эндпоинты (ручные действия владельца в панели);
  * исполнитель PendingAction (действия агента после аппрува в Telegram).

Функции бросают ValueError при бизнес-ошибках (нет юзера, нулевая delta) —
вызывающий сам решает, как это показать (HTTPException / текст в Telegram).
"""

import os

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AdminCreditGrant, AiRequest, BugReport, Download, EmailVerification, ExportTask, ParserTask,
    PasswordReset, PromoActivation, ReviewTask, SupportConversation, SupportMessage,
    Transaction, User,
)
from app.services.admin_incident_service import safe_text


def _required_comment(comment: str) -> str:
    normalized_comment = safe_text(str(comment or "").strip())
    if not normalized_comment:
        raise ValueError("Комментарий не может быть пустым")
    return normalized_comment


def grant_credits(db: Session, admin_id: int, user_id: int, delta: int,
                  comment: str, commit: bool = True) -> int:
    """Начислить/списать кредиты. По умолчанию коммитит сам."""
    comment = _required_comment(comment)
    delta = int(delta)
    if delta == 0:
        raise ValueError("delta не должна быть нулевой")
    user = db.get(User, user_id)
    if not user:
        raise ValueError("Пользователь не найден")
    user.credits_balance = max(0, user.credits_balance + delta)
    db.add(AdminCreditGrant(admin_id=admin_id, user_id=user_id, delta=delta,
                            comment=comment))
    if commit:
        db.commit()
    return user.credits_balance


def set_block(db: Session, user_id: int, blocked: bool, comment: str, commit: bool = True) -> bool:
    """Заблокировать/разблокировать юзера. По умолчанию коммитит сам."""
    _required_comment(comment)
    user = db.get(User, user_id)
    if not user:
        raise ValueError("Пользователь не найден")
    user.is_blocked = bool(blocked)
    if commit:
        db.commit()
    return user.is_blocked


def delete_user(db: Session, user_id: int) -> str:
    """Полностью удаляет пользователя и все связанные данные (задачи, выгрузки,
    транзакции, беседы, токены). Удаляет файлы выгрузок с диска. Коммитит сам.

    Возвращает email удалённого пользователя. Бросает ValueError при ошибке.
    """
    user = db.get(User, user_id)
    if not user:
        raise ValueError("Пользователь не найден")
    if user.is_admin:
        raise ValueError("Нельзя удалить администратора")
    email = user.email

    # 1. Удаляем файлы выгрузок с диска
    downloads = db.execute(select(Download).where(Download.user_id == user_id)).scalars().all()
    for d in downloads:
        if d.file_path and os.path.exists(d.file_path):
            try:
                os.remove(d.file_path)
            except OSError:
                pass

    # 2. Беседы поддержки + их сообщения
    conv_ids = db.execute(
        select(SupportConversation.id).where(SupportConversation.user_id == user_id)
    ).scalars().all()
    if conv_ids:
        db.execute(delete(SupportMessage).where(SupportMessage.conversation_id.in_(conv_ids)))
        db.execute(delete(SupportConversation).where(SupportConversation.user_id == user_id))

    # 3. Остальные связанные строки (дочерние — до удаления самого юзера)
    db.execute(delete(Download).where(Download.user_id == user_id))
    db.execute(delete(ExportTask).where(ExportTask.user_id == user_id))
    db.execute(delete(ParserTask).where(ParserTask.user_id == user_id))
    db.execute(delete(ReviewTask).where(ReviewTask.user_id == user_id))
    db.execute(delete(Transaction).where(Transaction.user_id == user_id))
    db.execute(delete(PromoActivation).where(PromoActivation.user_id == user_id))
    db.execute(delete(EmailVerification).where(EmailVerification.user_id == user_id))
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == user_id))
    db.execute(delete(BugReport).where(BugReport.user_id == user_id))
    db.execute(delete(AiRequest).where(AiRequest.user_id == user_id))
    db.execute(delete(AdminCreditGrant).where(
        or_(AdminCreditGrant.user_id == user_id, AdminCreditGrant.admin_id == user_id)))

    db.delete(user)
    db.commit()
    return email
