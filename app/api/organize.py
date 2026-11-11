from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import OrganizeBatch
from app.services.organize import (
    apply_organize,
    batch_to_dict,
    library_is_writable,
    preview_organize,
    rollback_batch,
)

router = APIRouter(prefix="/organize", tags=["organize"])


class OrganizeBody(BaseModel):
    template: str | None = None


@router.get("/preview")
def api_preview(template: str | None = None, session: Session = Depends(get_session)):
    return preview_organize(session, template)


@router.post("/apply")
def api_apply(body: OrganizeBody | None = None, session: Session = Depends(get_session)):
    settings = get_settings()
    if not settings.organize_enabled:
        raise HTTPException(status_code=403, detail="整理功能未启用（ORGANIZE_ENABLED=true）")
    if not library_is_writable():
        raise HTTPException(status_code=403, detail="书库目录不可写，请改为可写挂载后再执行")
    try:
        batch = apply_organize(session, body.template if body else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return batch_to_dict(batch)


@router.post("/rollback/{batch_id}")
def api_rollback(batch_id: int, session: Session = Depends(get_session)):
    try:
        batch = rollback_batch(session, batch_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return batch_to_dict(batch)


@router.get("/batches")
def api_batches(session: Session = Depends(get_session)):
    rows = session.scalars(select(OrganizeBatch).order_by(OrganizeBatch.id.desc()).limit(20)).all()
    return {"items": [batch_to_dict(b) for b in rows]}
