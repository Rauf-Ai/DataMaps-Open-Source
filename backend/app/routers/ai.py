from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""AI-эндпоинты для DataMaps: подбор фильтров по свободному промпту."""

import json
import logging

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Request
)
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.error_codes import api_error, classify_ai_error
from app.models import AiRequest, User
from app.ratelimit import limiter
from app.services.ai_filters_service import parse_filters_from_prompt


log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/parse-filters")
@limiter.limit("10/minute")
async def parse_filters(
    request: Request,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_id = getattr(request.state, "request_id", None)
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise api_error(400, "AI-001", "Введите задачу для AI", request_id=request_id)
    if len(prompt) > 2000:
        raise api_error(400, "AI-002", "Промпт слишком длинный (>2000 символов)", request_id=request_id)
    try:
        result = await parse_filters_from_prompt(prompt)
    except RuntimeError as exc:
        status, code, message, hint = classify_ai_error(str(exc))
        log.warning("AI parse-filters failed code=%s request_id=%s error=%s", code, request_id, exc)
        raise api_error(status, code, message, hint=hint, request_id=request_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("AI parse-filters unexpected code=AI-500 request_id=%s", request_id)
        raise api_error(
            500,
            "AI-500",
            "Внутренняя ошибка AI-подбора",
            hint="Посмотрите backend logs по request_id.",
            request_id=request_id,
            extra={"type": exc.__class__.__name__},
        )

    # Логируем запрос и предложенные критерии (для истории и аналитики)
    try:
        db.add(AiRequest(user_id=user.id, prompt=prompt,
                         filters_json=json.dumps(result.get("filters") or {}, ensure_ascii=False)))
        db.commit()
    except Exception:  # noqa: BLE001
        log.warning("AI request audit log failed code=AI-901 request_id=%s", request_id, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            log.warning("AI request audit rollback failed code=AI-902 request_id=%s", request_id, exc_info=True)
    return result
