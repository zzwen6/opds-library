from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import Author, Book, BookAuthor, Publisher, Series, Tag, BookTag
from app.schemas import BookDetail, BookUpdate, LockRequest, Page
from app.services.books import (
    book_to_detail,
    book_to_list_item,
    get_book_by_uuid,
    list_books,
    lock_field,
    unlock_field,
    update_book,
)

router = APIRouter(tags=["books"])


@router.get("/books")
def api_list_books(
    q: str | None = None,
    author_id: int | None = None,
    series_id: int | None = None,
    tag_id: int | None = None,
    publisher_id: int | None = None,
    language: str | None = None,
    format: str | None = None,
    scrape_status: str | None = None,
    sort: str = "added",
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=200),
    session: Session = Depends(get_session),
):
    size = page_size or get_settings().page_size
    rows, total = list_books(
        session,
        q=q,
        author_id=author_id,
        series_id=series_id,
        tag_id=tag_id,
        publisher_id=publisher_id,
        language=language,
        format=format,
        scrape_status=scrape_status,
        sort=sort,
        page=page,
        page_size=size,
    )
    return {
        "items": [book_to_list_item(b).model_dump() for b in rows],
        "total": total,
        "page": page,
        "page_size": size,
    }


@router.get("/books/{book_uuid}", response_model=BookDetail)
def api_get_book(book_uuid: str, session: Session = Depends(get_session)) -> BookDetail:
    book = get_book_by_uuid(session, book_uuid)
    if not book:
        raise HTTPException(status_code=404, detail="找不到这本书")
    return book_to_detail(book)


@router.patch("/books/{book_uuid}", response_model=BookDetail)
def api_update_book(
    book_uuid: str, payload: BookUpdate, session: Session = Depends(get_session)
) -> BookDetail:
    book = get_book_by_uuid(session, book_uuid)
    if not book:
        raise HTTPException(status_code=404, detail="找不到这本书")
    book = update_book(session, book, payload, auto_lock=True)
    return book_to_detail(book)


@router.post("/books/{book_uuid}/locks", response_model=BookDetail)
def api_toggle_lock(
    book_uuid: str, payload: LockRequest, session: Session = Depends(get_session)
) -> BookDetail:
    book = get_book_by_uuid(session, book_uuid)
    if not book:
        raise HTTPException(status_code=404, detail="找不到这本书")
    if payload.locked:
        lock_field(session, book, payload.field_name, payload.reason or "manual")
    else:
        unlock_field(session, book, payload.field_name)
    session.flush()
    book = get_book_by_uuid(session, book_uuid)
    return book_to_detail(book)


@router.get("/authors")
def api_authors(session: Session = Depends(get_session)):
    rows = session.execute(
        select(Author, func.count(BookAuthor.book_id))
        .outerjoin(BookAuthor)
        .group_by(Author.id)
        .order_by(Author.sort_name, Author.name)
    ).all()
    return [
        {"id": a.id, "uuid": a.uuid, "name": a.name, "book_count": count}
        for a, count in rows
    ]


@router.get("/series")
def api_series(session: Session = Depends(get_session)):
    rows = session.execute(
        select(Series, func.count(Book.id))
        .outerjoin(Book, Book.series_id == Series.id)
        .group_by(Series.id)
        .order_by(Series.sort_name, Series.name)
    ).all()
    return [
        {"id": s.id, "uuid": s.uuid, "name": s.name, "book_count": count}
        for s, count in rows
    ]


@router.get("/tags")
def api_tags(session: Session = Depends(get_session)):
    rows = session.execute(
        select(Tag, func.count(BookTag.book_id))
        .outerjoin(BookTag)
        .group_by(Tag.id)
        .order_by(Tag.name)
    ).all()
    return [
        {"id": t.id, "name": t.name, "kind": t.kind, "book_count": count}
        for t, count in rows
    ]


@router.get("/publishers")
def api_publishers(session: Session = Depends(get_session)):
    rows = session.scalars(select(Publisher).order_by(Publisher.name)).all()
    return [{"id": p.id, "name": p.name} for p in rows]


@router.get("/meta/filters")
def api_filters(session: Session = Depends(get_session)):
    languages = [
        row[0]
        for row in session.execute(
            select(Book.language).where(Book.language.is_not(None)).distinct()
        ).all()
        if row[0]
    ]
    return {"languages": sorted(languages)}
