from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.scrapers.service import confirm_candidate, list_queue, scrape_book
from app.services.books import book_to_detail, get_book_by_uuid

router = APIRouter(tags=["scrape"])


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
    return {"uuid": book.uuid, "scrape_status": book.scrape_status, "candidates": candidates}


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
