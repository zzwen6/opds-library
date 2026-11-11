from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.batch import _guard
from app.db import get_session
from app.services.duplicates import (
    IgnoreRequest,
    MergeRequest,
    apply_merge,
    find_duplicates,
    ignore_group,
    preview_merge,
    unignore_group,
)

router = APIRouter(tags=["duplicates"])


@router.get("/duplicates")
def api_duplicates(include_ignored: bool = False, session: Session = Depends(get_session)):
    return find_duplicates(session, include_ignored=include_ignored)


@router.post("/duplicates/ignore")
def api_ignore(req: IgnoreRequest, session: Session = Depends(get_session)):
    return ignore_group(session, req)


@router.post("/duplicates/unignore")
def api_unignore(req: IgnoreRequest, session: Session = Depends(get_session)):
    if not unignore_group(session, req.group_key):
        raise HTTPException(status_code=404, detail="该分组未被标记")
    return {"ok": True}


@router.post("/duplicates/merge/preview")
def api_merge_preview(req: MergeRequest, session: Session = Depends(get_session)):
    return _guard(preview_merge, session, req)


@router.post("/duplicates/merge")
def api_merge(req: MergeRequest, session: Session = Depends(get_session)):
    return _guard(apply_merge, session, req)
