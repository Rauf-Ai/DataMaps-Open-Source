from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime, timedelta

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_admin_user
from app.models import (
    AiRequest, BugReport, Download, Package, ParserTask, PendingAction, PromoActivation, PromoCode,
    ReviewTask, SupportConversation, SupportMessage, Transaction, TransactionStatus, User,
    UserSubscription,
)
from app.config import get_settings
from app.services import admin_actions, admin_audit_service, email_service, support_service
from app.services.admin_incident_service import safe_text
from app.services.subscription_service import (
    PLAN_CATALOG,
    assign_plan_manually,
    get_or_create_subscription,
    sync_subscription_period,
)

settings = get_settings()
from app.timeutils import utcnow


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_admin_user)])


def _required_comment(payload: dict) -> str:
    comment = safe_text(str(payload.get("comment") or "").strip())
    if not comment:
        raise HTTPException(status_code=400, detail="Комментарий не может быть пустым")
    return comment


@router.get("/dashboard")
async def dashboard(db: Annotated[Session, Depends(get_db)]):
    now = utcnow()
    d7 = now - timedelta(days=7)
    d30 = now - timedelta(days=30)

    total_users = db.scalar(select(func.count(User.id)))
    new_users_7d = db.scalar(select(func.count(User.id)).where(User.created_at >= d7))

    revenue_7d_kop = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount_kop), 0)).where(
            Transaction.status == TransactionStatus.succeeded, Transaction.completed_at >= d7
        )
    )
    revenue_30d_kop = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount_kop), 0)).where(
            Transaction.status == TransactionStatus.succeeded, Transaction.completed_at >= d30
        )
    )
    downloads_7d = db.scalar(select(func.count(Download.id)).where(Download.created_at >= d7))

    # Продажи по дням за 30 дней
    sales_rows = db.execute(
        select(func.date(Transaction.completed_at), func.sum(Transaction.amount_kop))
        .where(Transaction.status == TransactionStatus.succeeded, Transaction.completed_at >= d30)
        .group_by(func.date(Transaction.completed_at))
        .order_by(func.date(Transaction.completed_at))
    ).all()

    recent_tx = db.execute(
        select(Transaction, User, Package)
        .join(User, User.id == Transaction.user_id)
        .join(Package, Package.id == Transaction.package_id)
        .order_by(Transaction.created_at.desc())
        .limit(10)
    ).all()

    return {
        "total_users": total_users,
        "new_users_7d": new_users_7d,
        "revenue_7d_rub": (revenue_7d_kop or 0) / 100,
        "revenue_30d_rub": (revenue_30d_kop or 0) / 100,
        "downloads_7d": downloads_7d,
        "sales_by_day": [{"date": str(d), "amount_rub": (a or 0) / 100} for d, a in sales_rows],
        "recent_transactions": [
            {
                "id": t.id,
                "user_email": u.email,
                "package_name": p.name,
                "amount_rub": t.amount_kop / 100,
                "credits": t.credits,
                "status": t.status.value,
                "created_at": t.created_at.isoformat(),
            }
            for t, u, p in recent_tx
        ],
    }


# ─────── Current subscription plans + legacy package CRUD ───────

@router.get("/subscription-plans")
async def adm_subscription_plans():
    return [
        {
            "code": plan.code,
            "name": plan.name,
            "price_rub": plan.price_kop / 100,
            "organizations": plan.organizations,
            "reviews": plan.reviews,
            "enrichment": plan.enrichment,
            "seasonality": plan.seasonality,
            "social": plan.social,
            "concurrent_tasks": plan.concurrent_tasks,
            "queue": "Приоритетная" if plan.queue_priority >= 20 else "Ускоренная" if plan.queue_priority >= 10 else "Обычная",
        }
        for plan in PLAN_CATALOG.values()
    ]

@router.get("/packages")
async def adm_packages(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(select(Package).order_by(Package.sort_order, Package.id)).scalars().all()
    return [
        {
            "id": p.id, "name": p.name, "credits": p.credits, "price_kop": p.price_kop,
            "price_rub": p.price_kop / 100, "is_active": p.is_active, "sort_order": p.sort_order,
        } for p in rows
    ]


@router.post("/packages")
async def adm_packages_create(payload: dict, db: Annotated[Session, Depends(get_db)]):
    p = Package(
        name=str(payload["name"]),
        credits=int(payload["credits"]),
        price_kop=int(round(float(payload["price_rub"]) * 100)),
        is_active=bool(payload.get("is_active", True)),
        sort_order=int(payload.get("sort_order", 0)),
    )
    db.add(p)
    db.commit()
    return {"ok": True, "id": p.id}


@router.patch("/packages/{pid}")
async def adm_packages_update(pid: int, payload: dict, db: Annotated[Session, Depends(get_db)]):
    p = db.get(Package, pid)
    if not p:
        raise HTTPException(status_code=404, detail="Не найдено")
    if "name" in payload:
        p.name = str(payload["name"])
    if "credits" in payload:
        p.credits = int(payload["credits"])
    if "price_rub" in payload:
        p.price_kop = int(round(float(payload["price_rub"]) * 100))
    if "is_active" in payload:
        p.is_active = bool(payload["is_active"])
    if "sort_order" in payload:
        p.sort_order = int(payload["sort_order"])
    db.commit()
    return {"ok": True}


@router.delete("/packages/{pid}")
async def adm_packages_delete(pid: int, db: Annotated[Session, Depends(get_db)]):
    p = db.get(Package, pid)
    if not p:
        raise HTTPException(status_code=404, detail="Не найдено")
    # Не удаляем, а скрываем — на пакете могут висеть транзакции
    p.is_active = False
    db.commit()
    return {"ok": True}


# ─────── Users ───────

@router.get("/users")
async def adm_users(q: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = (
        select(User, UserSubscription)
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
        .order_by(User.created_at.desc())
        .limit(200)
    )
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q}%"))
    rows = db.execute(stmt).all()
    result = []
    subscriptions_changed = False
    for user, subscription in rows:
        if subscription:
            previous = (subscription.plan_code, subscription.status, subscription.period_end)
            sync_subscription_period(subscription)
            subscriptions_changed = subscriptions_changed or previous != (
                subscription.plan_code, subscription.status, subscription.period_end
            )
            plan_code = subscription.plan_code
            status = subscription.status.value
            period_end = subscription.period_end.isoformat()
        else:
            plan_code = "free"
            status = "active"
            period_end = None
        plan = PLAN_CATALOG.get(plan_code, PLAN_CATALOG["free"])
        result.append({
            "id": user.id, "email": user.email, "is_admin": user.is_admin,
            "is_blocked": user.is_blocked,
            "credits_balance": user.credits_balance, "credits_spent": user.credits_spent,
            "total_paid_rub": user.total_paid_kop / 100,
            "created_at": user.created_at.isoformat(),
            "email_verified": user.email_verified_at is not None,
            "plan_code": plan_code,
            "plan_name": plan.name,
            "subscription_status": status,
            "subscription_period_end": period_end,
        })
    if subscriptions_changed:
        db.commit()
    return result


@router.post("/users/{uid}/plan")
async def adm_assign_plan(
    uid: int,
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_admin_user)],
    request: Request,
):
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    plan_code = str(payload.get("plan_code") or "").strip().lower()
    if plan_code not in PLAN_CATALOG:
        raise HTTPException(status_code=400, detail="Неизвестный тариф")
    comment = _required_comment(payload)

    persisted_plan_code = db.scalar(
        select(UserSubscription.plan_code).where(UserSubscription.user_id == uid)
    )
    before = {"plan_code": persisted_plan_code or "free"}
    subscription = get_or_create_subscription(db, uid)
    try:
        assign_plan_manually(subscription, plan_code)
        admin_audit_service.write_audit(
            db,
            admin_id=admin.id,
            action="user.plan.assign",
            target_type="user",
            target_id=uid,
            before=before,
            after={"plan_code": plan_code},
            comment=comment,
            request_id=getattr(request.state, "request_id", None),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    plan = PLAN_CATALOG[plan_code]
    return {
        "ok": True,
        "user_id": uid,
        "plan_code": plan_code,
        "plan_name": plan.name,
        "subscription_status": subscription.status.value,
        "subscription_period_end": subscription.period_end.isoformat(),
        "assigned_by": admin.id,
    }


@router.post("/users/{uid}/credits")
async def adm_grant_credits(
    uid: int,
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_admin_user)],
    request: Request,
):
    comment = _required_comment(payload)
    try:
        user = db.get(User, uid)
        if not user:
            raise ValueError("Пользователь не найден")
        before = {"credits_balance": user.credits_balance}
        delta = int(payload.get("delta") or 0)
        balance = admin_actions.grant_credits(
            db, admin.id, uid, delta, comment, commit=False)
    except ValueError as exc:
        db.rollback()
        status = 404 if "не найден" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))

    try:
        admin_audit_service.write_audit(
            db,
            admin_id=admin.id,
            action="user.credits.adjust",
            target_type="user",
            target_id=uid,
            before=before,
            after={"credits_balance": balance, "delta": delta},
            comment=comment,
            request_id=getattr(request.state, "request_id", None),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "credits_balance": balance}


# ─────── Promo Codes ───────

@router.get("/promos")
async def adm_promos(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(select(PromoCode).order_by(PromoCode.created_at.desc())).scalars().all()
    return [
        {
            "id": p.id, "code": p.code, "credits": p.credits,
            "discount_percent": p.discount_percent,
            "max_activations": p.max_activations, "used_count": p.used_count,
            "is_active": p.is_active,
            "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            "created_at": p.created_at.isoformat(),
        } for p in rows
    ]


@router.post("/promos")
async def adm_promos_create(payload: dict, db: Annotated[Session, Depends(get_db)]):
    code = str(payload.get("code", "")).strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Код не может быть пустым")
    existing = db.execute(select(PromoCode).where(PromoCode.code == code)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Промокод с таким кодом уже существует")
    expires_at = None
    if payload.get("expires_at"):
        expires_at = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
    p = PromoCode(
        code=code,
        credits=int(payload.get("credits") or 0),
        discount_percent=min(100, max(0, int(payload.get("discount_percent") or 0))),
        max_activations=int(payload.get("max_activations") or 0),
        is_active=bool(payload.get("is_active", True)),
        expires_at=expires_at,
    )
    db.add(p)
    db.commit()
    return {"ok": True, "id": p.id}


@router.patch("/promos/{pid}")
async def adm_promos_update(pid: int, payload: dict, db: Annotated[Session, Depends(get_db)]):
    p = db.get(PromoCode, pid)
    if not p:
        raise HTTPException(status_code=404, detail="Не найдено")
    if "code" in payload:
        p.code = str(payload["code"]).strip().upper()
    if "credits" in payload:
        p.credits = int(payload["credits"])
    if "discount_percent" in payload:
        p.discount_percent = min(100, max(0, int(payload["discount_percent"])))
    if "max_activations" in payload:
        p.max_activations = int(payload["max_activations"])
    if "is_active" in payload:
        p.is_active = bool(payload["is_active"])
    if "expires_at" in payload:
        if payload["expires_at"]:
            p.expires_at = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            p.expires_at = None
    db.commit()
    return {"ok": True}


@router.delete("/promos/{pid}")
async def adm_promos_delete(pid: int, db: Annotated[Session, Depends(get_db)]):
    p = db.get(PromoCode, pid)
    if not p:
        raise HTTPException(status_code=404, detail="Не найдено")
    p.is_active = False
    db.commit()
    return {"ok": True}


@router.post("/users/{uid}/block")
async def adm_block_user(
    uid: int,
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_admin_user)],
    request: Request,
):
    comment = _required_comment(payload)
    try:
        user = db.get(User, uid)
        if not user:
            raise ValueError("Пользователь не найден")
        before = {"is_blocked": user.is_blocked}
        is_blocked = admin_actions.set_block(
            db, uid, bool(payload.get("blocked", True)), comment, commit=False
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404 if "не найден" in str(exc) else 400, detail=str(exc))

    try:
        admin_audit_service.write_audit(
            db,
            admin_id=admin.id,
            action="user.block.set",
            target_type="user",
            target_id=uid,
            before=before,
            after={"is_blocked": is_blocked},
            comment=comment,
            request_id=getattr(request.state, "request_id", None),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "is_blocked": is_blocked}


@router.delete("/users/{uid}")
async def adm_delete_user(
    uid: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_admin_user)],
):
    if uid == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    try:
        email = admin_actions.delete_user(db, uid)
    except ValueError as exc:
        status = 404 if "не найден" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc))
    return {"ok": True, "deleted_email": email}


@router.get("/users/{uid}/activity")
async def adm_user_activity(uid: int, db: Annotated[Session, Depends(get_db)]):
    import json as _json
    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    downloads = db.execute(
        select(Download).where(Download.user_id == uid)
        .order_by(Download.created_at.desc()).limit(50)
    ).scalars().all()

    def _dl_kind(fj):
        try:
            d = _json.loads(fj) if fj else {}
            return d.get("kind") or "export"
        except (ValueError, TypeError):
            return "export"

    parser_tasks = db.execute(
        select(ParserTask).where(ParserTask.user_id == uid)
        .order_by(ParserTask.created_at.desc()).limit(50)
    ).scalars().all()
    review_tasks = db.execute(
        select(ReviewTask).where(ReviewTask.user_id == uid)
        .order_by(ReviewTask.created_at.desc()).limit(50)
    ).scalars().all()
    txns = db.execute(
        select(Transaction, Package.name)
        .join(Package, Package.id == Transaction.package_id)
        .where(Transaction.user_id == uid)
        .order_by(Transaction.created_at.desc()).limit(50)
    ).all()
    ai_requests = db.execute(
        select(AiRequest).where(AiRequest.user_id == uid)
        .order_by(AiRequest.created_at.desc()).limit(50)
    ).scalars().all()

    def _params(pj):
        try:
            return _json.loads(pj or "{}")
        except (ValueError, TypeError):
            return {}

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "credits_balance": user.credits_balance,
            "credits_spent": user.credits_spent,
            "created_at": user.created_at.isoformat(),
        },
        "downloads": [
            {
                "id": d.id,
                "kind": _dl_kind(d.filters_json),
                "row_count": d.row_count,
                "credits_charged": d.credits_charged,
                "available": bool(d.file_path),
                "created_at": d.created_at.isoformat(),
            }
            for d in downloads
        ],
        "parser_tasks": [
            {
                "id": t.id,
                "kind": t.kind,
                "source": t.source,
                "status": t.status.value,
                "query": _params(t.params_json).get("query") or _params(t.params_json).get("url"),
                "org_count": t.org_count,
                "credits_charged": t.credits_charged,
                "created_at": t.created_at.isoformat(),
            }
            for t in parser_tasks
        ],
        "review_tasks": [
            {
                "id": t.id,
                "status": t.status.value,
                "credits_charged": t.credits_charged,
                "created_at": t.created_at.isoformat(),
            }
            for t in review_tasks
        ],
        "ai_requests": [
            {
                "id": a.id,
                "prompt": a.prompt,
                "filters": _params(a.filters_json),
                "created_at": a.created_at.isoformat(),
            }
            for a in ai_requests
        ],
        "transactions": [
            {
                "id": t.id,
                "package_name": name,
                "amount_rub": t.amount_kop / 100,
                "credits": t.credits,
                "status": t.status.value,
                "created_at": t.created_at.isoformat(),
            }
            for t, name in txns
        ],
    }


@router.get("/review-tasks")
async def adm_review_tasks(db: Annotated[Session, Depends(get_db)]):
    import json as _json
    rows = db.execute(
        select(ReviewTask, User.email)
        .join(User, User.id == ReviewTask.user_id)
        .order_by(ReviewTask.created_at.desc())
        .limit(100)
    ).all()
    out = []
    for t, email in rows:
        try:
            org_count = len(_json.loads(t.org_ids_json or "[]"))
        except _json.JSONDecodeError:
            org_count = 0
        out.append({
            "id": t.id,
            "user_email": email,
            "status": t.status.value,
            "progress": t.progress,
            "error": t.error,
            "org_count": org_count,
            "star_min": t.star_min,
            "star_max": t.star_max,
            "credits_charged": t.credits_charged,
            "created_at": t.created_at.isoformat(),
        })
    return out


# ─────── Поддержка ───────
@router.get("/support/conversations")
async def adm_support_conversations(
    db: Annotated[Session, Depends(get_db)],
    needs_human: bool = False,
):
    stmt = (
        select(SupportConversation, User.email)
        .outerjoin(User, User.id == SupportConversation.user_id)
        .order_by(SupportConversation.last_message_at.desc())
        .limit(100)
    )
    if needs_human:
        stmt = stmt.where(SupportConversation.status == "needs_human")
    rows = db.execute(stmt).all()
    return [
        {
            "id": c.id,
            "user_email": email,
            "guest": c.user_id is None,
            "status": c.status,
            "last_message_at": c.last_message_at.isoformat(),
            "created_at": c.created_at.isoformat(),
        }
        for c, email in rows
    ]


@router.get("/support/conversations/{cid}")
async def adm_support_thread(cid: int, db: Annotated[Session, Depends(get_db)]):
    conv = db.get(SupportConversation, cid)
    if not conv:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    rows = db.execute(
        select(SupportMessage).where(SupportMessage.conversation_id == cid)
        .order_by(SupportMessage.created_at)
    ).scalars().all()
    user_info = None
    if conv.user_id:
        u = db.get(User, conv.user_id)
        if u:
            user_info = {
                "id": u.id, "email": u.email, "is_blocked": u.is_blocked,
                "is_admin": u.is_admin, "credits_balance": u.credits_balance,
            }
    return {
        "id": conv.id,
        "status": conv.status,
        "user": user_info,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                **({"image_url": f"/api/support/attachment/{m.id}"} if m.image_path else {}),
            }
            for m in rows
        ],
    }


@router.post("/support/conversations/{cid}/reply")
async def adm_support_reply(cid: int, payload: dict, db: Annotated[Session, Depends(get_db)]):
    conv = db.get(SupportConversation, cid)
    if not conv:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой ответ")
    db.add(SupportMessage(conversation_id=cid, role="admin", content=text))
    conv.last_message_at = utcnow()
    if conv.status == "needs_human":
        conv.status = "open"
    # email пользователю до коммита — берём адрес, сам коммит ниже
    to_email = None
    if conv.user_id:
        u = db.get(User, conv.user_id)
        to_email = u.email if u else None
    db.commit()
    # Уведомляем пользователя по почте (после коммита, без блокировки БД)
    if to_email:
        await email_service.send_support_reply_email(
            to_email, text, f"{settings.app_url.rstrip('/')}/dashboard")
    return {"ok": True}


@router.post("/support/conversations/{cid}/close")
async def adm_support_close(cid: int, db: Annotated[Session, Depends(get_db)]):
    conv = db.get(SupportConversation, cid)
    if not conv:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    conv.status = "closed"
    db.commit()
    return {"ok": True, "status": "closed"}


@router.get("/support/pending-actions")
async def adm_pending_actions(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(
        select(PendingAction).order_by(PendingAction.created_at.desc()).limit(100)
    ).scalars().all()
    return [
        {
            "id": pa.id,
            "kind": pa.kind,
            "target_user_id": pa.target_user_id,
            "summary": pa.summary,
            "status": pa.status,
            "result": pa.result,
            "conversation_id": pa.conversation_id,
            "created_at": pa.created_at.isoformat(),
            "resolved_at": pa.resolved_at.isoformat() if pa.resolved_at else None,
        }
        for pa in rows
    ]


@router.post("/support/pending-actions/{pa_id}/resolve")
async def adm_resolve_action(
    pa_id: str,
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_admin_user)],
):
    pa = db.get(PendingAction, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="Действие не найдено")
    decision = (payload.get("decision") or "").lower()
    if decision == "approve":
        ok, result = support_service.execute_pending_action(db, pa, approver_admin_id=admin.id)
        return {"ok": ok, "result": result, "status": pa.status}
    if decision == "reject":
        support_service.reject_pending_action(db, pa)
        return {"ok": True, "status": pa.status}
    raise HTTPException(status_code=400, detail="decision: approve | reject")


@router.get("/support/bug-reports")
async def adm_bug_reports(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(
        select(BugReport).order_by(BugReport.created_at.desc()).limit(100)
    ).scalars().all()
    return [
        {
            "id": b.id,
            "title": b.title,
            "description": b.description,
            "proposed_fix": b.proposed_fix,
            "severity": b.severity,
            "status": b.status,
            "conversation_id": b.conversation_id,
            "created_at": b.created_at.isoformat(),
        }
        for b in rows
    ]


# ─────── Аналитика парсинга ───────
@router.get("/analytics/parsing")
async def adm_analytics_parsing(db: Annotated[Session, Depends(get_db)]):
    """Агрегаты по «Сбору данных»: что и кто чаще парсит + последние AI-промпты."""
    import json as _json
    from collections import Counter

    tasks = db.execute(
        select(ParserTask).order_by(ParserTask.created_at.desc()).limit(5000)
    ).scalars().all()
    queries: Counter = Counter()
    sources: Counter = Counter()
    kinds: Counter = Counter()
    user_counts: Counter = Counter()
    for t in tasks:
        try:
            params = _json.loads(t.params_json or "{}")
        except (ValueError, TypeError):
            params = {}
        q = (params.get("query") or params.get("url") or "").strip().lower()
        if q:
            queries[q] += 1
        sources[t.source or "?"] += 1
        kinds[t.kind or "?"] += 1
        user_counts[t.user_id] += 1

    top_user_ids = [uid for uid, _ in user_counts.most_common(10)]
    emails = {}
    if top_user_ids:
        emails = {
            u.id: u.email
            for u in db.execute(select(User).where(User.id.in_(top_user_ids))).scalars().all()
        }

    ai_rows = db.execute(
        select(AiRequest, User.email).join(User, User.id == AiRequest.user_id)
        .order_by(AiRequest.created_at.desc()).limit(30)
    ).all()

    return {
        "total_tasks": len(tasks),
        "top_queries": [{"query": q, "count": c} for q, c in queries.most_common(15)],
        "by_source": [{"source": s, "count": c} for s, c in sources.most_common()],
        "by_kind": [{"kind": k, "count": c} for k, c in kinds.most_common()],
        "top_users": [{"email": emails.get(uid, f"#{uid}"), "count": user_counts[uid]}
                      for uid in top_user_ids],
        "recent_ai": [
            {"email": email, "prompt": a.prompt[:200], "created_at": a.created_at.isoformat()}
            for a, email in ai_rows
        ],
    }
