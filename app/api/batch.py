from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.batch_edit import (
    AuthorMergeRequest,
    BatchEditRequest,
    apply_author_merge,
    apply_batch_edit,
    author_variants,
    list_batches,
    preview_author_merge,
    preview_batch_edit,
    undo_batch,
)

router = APIRouter(tags=["batch"])


def _guard(fn, *args):
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batch/preview")
def api_batch_preview(req: BatchEditRequest, session: Session = Depends(get_session)):
    return _guard(preview_batch_edit, session, req)


@router.post("/batch/apply")
def api_batch_apply(req: BatchEditRequest, session: Session = Depends(get_session)):
    return _guard(apply_batch_edit, session, req)


@router.get("/authors/variants")
def api_author_variants(session: Session = Depends(get_session)):
    return author_variants(session)


@router.post("/authors/merge/preview")
def api_author_merge_preview(req: AuthorMergeRequest, session: Session = Depends(get_session)):
    return _guard(preview_author_merge, session, req)


@router.post("/authors/merge")
def api_author_merge(req: AuthorMergeRequest, session: Session = Depends(get_session)):
    return _guard(apply_author_merge, session, req)


@router.get("/edit-batches")
def api_edit_batches(limit: int = 50, session: Session = Depends(get_session)):
    return list_batches(session, limit=min(max(limit, 1), 200))


@router.post("/edit-batches/{batch_id}/undo")
def api_undo_batch(batch_id: int, session: Session = Depends(get_session)):
    return _guard(undo_batch, session, batch_id)
