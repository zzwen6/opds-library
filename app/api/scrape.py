from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.models import Job
from app.scrapers.service import (
    confirm_candidate,
    latest_auto_record,
    list_queue,
    scrape_book,
    scrape_pending_books,
    undo_auto_apply,
)
from app.services.books import book_to_detail, get_book_by_uuid

router = APIRouter(tags=["scrape"])

_batch_lock = threading.Lock()
_batch_running = False
_batch_result: dict | None = None
_batch_error: str | None = None


class ConfirmBody(BaseModel):
    record_id: int


@router.get("/scrape/queue")
def api_queue(session: Session = Depends(get_session)):
    return {"items": list_queue(session)}


@router.post("/books/{book_uuid}/scrape")
def api_scrape(book_uuid: str, session: Session = Depends(get_session)):
    book = get_book_by_uuid(session, book_uuid)
    if not book:
        raise HTTPException(status_code=404, detail="找不到这本书")
    candidates = scrape_book(session, book)
    book = get_book_by_uuid(session, book_uuid) or book
    return {
        "uuid": book.uuid,
        "scrape_status": book.scrape_status,
        "auto_applied": book.scrape_status == "auto_applied",
        "candidates": candidates,
        "book": book_to_detail(book),
    }


@router.post("/books/{book_uuid}/scrape/confirm")
def api_confirm(book_uuid: str, body: ConfirmBody, session: Session = Depends(get_session)):
    book = get_book_by_uuid(session, book_uuid)
    if not book:
        raise HTTPException(status_code=404, detail="找不到这本书")
    try:
        book = confirm_candidate(session, book, body.record_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return book_to_detail(book)


@router.post("/books/{book_uuid}/scrape/undo")
def api_undo(book_uuid: str, session: Session = Depends(get_session)):
    book = get_book_by_uuid(session, book_uuid)
    if not book:
        raise HTTPException(status_code=404, detail="找不到这本书")
    try:
        book = undo_auto_apply(session, book)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return book_to_detail(book)


def _run_batch() -> None:
    global _batch_running, _batch_result, _batch_error
    session = SessionLocal()
    job = Job(type="scrape_batch", status="running", started_at=datetime.now(timezone.utc))
    session.add(job)
    session.commit()
    try:
        result = scrape_pending_books(session)
        session.commit()
        job.status = "done"
        job.result_json = json.dumps(result, ensure_ascii=False)
        job.finished_at = datetime.now(timezone.utc)
        session.commit()
        _batch_result = result
        _batch_error = None
    except Exception as exc:
        session.rollback()
        job.status = "failed"
        job.last_error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()
        _batch_error = str(exc)
    finally:
        session.close()
        with _batch_lock:
            _batch_running = False


@router.post("/scrape/batch", status_code=202)
def api_batch_scrape():
    global _batch_running
    with _batch_lock:
        if _batch_running:
            raise HTTPException(status_code=409, detail="批量刮削正在进行")
        _batch_running = True
    threading.Thread(target=_run_batch, daemon=True).start()
    return {"ok": True, "message": "已开始对未刮削书目批量自动刮削"}


@router.get("/scrape/batch/status")
def api_batch_status():
    return {"running": _batch_running, "last_result": _batch_result, "last_error": _batch_error}
