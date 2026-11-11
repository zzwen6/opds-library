from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Author,
    Book,
    BookAuthor,
    BookFile,
    BookTag,
    Cover,
    FieldLock,
    Publisher,
    Series,
    Tag,
)
from app.schemas import (
    BookDetail,
    BookListItem,
    BookUpdate,
    CoverOut,
    FieldLockOut,
    FileOut,
)
from app.util import normalize_isbn, normalize_space, sort_key

EDITABLE_FIELDS = {
    "title",
    "subtitle",
    "isbn13",
    "isbn10",
    "language",
    "description",
    "published_date",
    "page_count",
    "rating",
    "series_index",
    "authors",
    "publisher",
    "series",
    "tags",
    "cover",
}


def _cover_urls(book: Book) -> tuple[str | None, str | None]:
    cover = book.primary_cover
    if not cover and book.covers:
        cover = next((c for c in book.covers if c.is_primary), book.covers[0])
    if not cover:
        return None, None
    return f"/api/covers/{cover.id}", f"/api/covers/{cover.id}/thumb"


def book_to_list_item(book: Book) -> BookListItem:
    cover_url, thumb_url = _cover_urls(book)
    authors = [
        ba.author.name
        for ba in sorted(book.authors, key=lambda x: x.position)
        if ba.author
    ]
    formats = sorted({f.format for f in book.files if f.missing_at is None})
    return BookListItem(
        uuid=book.uuid,
        title=book.title,
        subtitle=book.subtitle,
        authors=authors,
        language=book.language,
        isbn13=book.isbn13,
        formats=formats,
        scrape_status=book.scrape_status,
        added_at=book.added_at,
        cover_url=cover_url,
        thumb_url=thumb_url,
        series=book.series.name if book.series else None,
    )


def book_to_detail(book: Book) -> BookDetail:
    item = book_to_list_item(book)
    covers = []
    for cover in book.covers:
        covers.append(
            CoverOut(
                id=cover.id,
                source=cover.source,
                mime=cover.mime,
                width=cover.width,
                height=cover.height,
                is_primary=cover.is_primary,
                url=f"/api/covers/{cover.id}",
                thumb_url=f"/api/covers/{cover.id}/thumb",
            )
        )
    return BookDetail(
        **item.model_dump(),
        description=book.description,
        isbn10=book.isbn10,
        publisher=book.publisher.name if book.publisher else None,
        series_index=book.series_index,
        published_date=book.published_date,
        page_count=book.page_count,
        rating=book.rating,
        tags=[bt.tag.name for bt in book.tags if bt.tag],
        files=[
            FileOut(
                id=f.id,
                rel_path=f.rel_path,
                filename=f.filename,
                format=f.format,
                size_bytes=f.size_bytes,
                sha256=f.sha256,
                missing=f.missing_at is not None,
            )
            for f in book.files
        ],
        locks=[
            FieldLockOut(field_name=lock.field_name, locked_at=lock.locked_at, reason=lock.reason)
            for lock in book.locks
        ],
        covers=covers,
        field_sources=json.loads(book.field_sources_json or "{}"),
        can_undo_auto=any(
            r.status == "auto_applied" and r.applied_mode == "auto" for r in book.scrape_records
        ),
    )


def book_query_options():
    return (
        selectinload(Book.authors).selectinload(BookAuthor.author),
        selectinload(Book.files),
        selectinload(Book.tags).selectinload(BookTag.tag),
        selectinload(Book.locks),
        selectinload(Book.covers),
        selectinload(Book.publisher),
        selectinload(Book.series),
        selectinload(Book.primary_cover),
        selectinload(Book.scrape_records),
    )


def list_books(
    session: Session,
    q: str | None = None,
    author_id: int | None = None,
    series_id: int | None = None,
    tag_id: int | None = None,
    publisher_id: int | None = None,
    language: str | None = None,
    format: str | None = None,
    scrape_status: str | None = None,
    sort: str = "added",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Book], int]:
    stmt = select(Book).options(*book_query_options())
    count_stmt = select(func.count(Book.id))

    if q:
        like = f"%{q.strip()}%"
        author_ids = select(BookAuthor.book_id).join(Author).where(Author.name.ilike(like))
        filt = or_(
            Book.title.ilike(like),
            Book.subtitle.ilike(like),
            Book.isbn13.ilike(like),
            Book.isbn10.ilike(like),
            Book.description.ilike(like),
            Book.id.in_(author_ids),
        )
        stmt = stmt.where(filt)
        count_stmt = count_stmt.where(filt)
    if author_id:
        stmt = stmt.where(Book.id.in_(select(BookAuthor.book_id).where(BookAuthor.author_id == author_id)))
        count_stmt = count_stmt.where(
            Book.id.in_(select(BookAuthor.book_id).where(BookAuthor.author_id == author_id))
        )
    if series_id:
        stmt = stmt.where(Book.series_id == series_id)
        count_stmt = count_stmt.where(Book.series_id == series_id)
    if tag_id:
        stmt = stmt.where(Book.id.in_(select(BookTag.book_id).where(BookTag.tag_id == tag_id)))
        count_stmt = count_stmt.where(Book.id.in_(select(BookTag.book_id).where(BookTag.tag_id == tag_id)))
    if publisher_id:
        stmt = stmt.where(Book.publisher_id == publisher_id)
        count_stmt = count_stmt.where(Book.publisher_id == publisher_id)
    if language:
        stmt = stmt.where(Book.language == language)
        count_stmt = count_stmt.where(Book.language == language)
    if format:
        file_ids = select(BookFile.book_id).where(BookFile.format == format, BookFile.missing_at.is_(None))
        stmt = stmt.where(Book.id.in_(file_ids))
        count_stmt = count_stmt.where(Book.id.in_(file_ids))
    if scrape_status:
        stmt = stmt.where(Book.scrape_status == scrape_status)
        count_stmt = count_stmt.where(Book.scrape_status == scrape_status)

    if sort == "title":
        stmt = stmt.order_by(func.lower(Book.sort_title), func.lower(Book.title))
    elif sort == "author":
        stmt = stmt.order_by(Book.id)
    else:
        stmt = stmt.order_by(Book.added_at.desc())

    total = session.scalar(count_stmt) or 0
    page = max(page, 1)
    rows = session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).unique().all()
    return list(rows), int(total)


def get_book_by_uuid(session: Session, book_uuid: str) -> Book | None:
    return session.scalar(select(Book).options(*book_query_options()).where(Book.uuid == book_uuid))


def lock_field(session: Session, book: Book, field_name: str, reason: str | None = "manual") -> FieldLock:
    existing = session.scalar(
        select(FieldLock).where(FieldLock.book_id == book.id, FieldLock.field_name == field_name)
    )
    if existing:
        existing.reason = reason
        existing.locked_at = datetime.now(timezone.utc)
        return existing
    lock = FieldLock(book_id=book.id, field_name=field_name, reason=reason)
    session.add(lock)
    session.flush()
    return lock


def unlock_field(session: Session, book: Book, field_name: str) -> None:
    existing = session.scalar(
        select(FieldLock).where(FieldLock.book_id == book.id, FieldLock.field_name == field_name)
    )
    if existing:
        session.delete(existing)


def locked_fields(book: Book) -> set[str]:
    return {lock.field_name for lock in book.locks}


def _set_authors(session: Session, book: Book, names: list[str]) -> None:
    session.execute(delete(BookAuthor).where(BookAuthor.book_id == book.id))
    for i, raw in enumerate(names):
        name = normalize_space(raw)
        if not name:
            continue
        author = session.scalar(select(Author).where(Author.name == name))
        if not author:
            author = Author(name=name, sort_name=sort_key(name))
            session.add(author)
            session.flush()
        session.add(BookAuthor(book_id=book.id, author_id=author.id, role="author", position=i))


def _set_tags(session: Session, book: Book, names: list[str]) -> None:
    session.execute(delete(BookTag).where(BookTag.book_id == book.id))
    for raw in names:
        name = normalize_space(raw)
        if not name:
            continue
        tag = session.scalar(select(Tag).where(Tag.name == name))
        if not tag:
            tag = Tag(name=name, kind="user")
            session.add(tag)
            session.flush()
        session.add(BookTag(book_id=book.id, tag_id=tag.id))


def update_book(session: Session, book: Book, payload: BookUpdate, auto_lock: bool = True) -> Book:
    data = payload.model_dump(exclude_unset=True)
    if "isbn13" in data and data["isbn13"]:
        isbn13, isbn10 = normalize_isbn(data["isbn13"])
        if isbn13:
            data["isbn13"] = isbn13
            data.setdefault("isbn10", isbn10)

    simple = {
        "title",
        "subtitle",
        "isbn13",
        "isbn10",
        "language",
        "description",
        "published_date",
        "page_count",
        "rating",
        "series_index",
    }
    for field in simple:
        if field not in data:
            continue
        value = data[field]
        if field == "title" and value:
            book.title = value
            book.sort_title = sort_key(value)
        else:
            setattr(book, field, value)
        if auto_lock:
            lock_field(session, book, field)

    if "authors" in data:
        _set_authors(session, book, data["authors"] or [])
        if auto_lock:
            lock_field(session, book, "authors")
    if "tags" in data:
        _set_tags(session, book, data["tags"] or [])
        if auto_lock:
            lock_field(session, book, "tags")
    if "publisher" in data:
        name = normalize_space(data["publisher"])
        if not name:
            book.publisher_id = None
        else:
            pub = session.scalar(select(Publisher).where(Publisher.name == name))
            if not pub:
                pub = Publisher(name=name)
                session.add(pub)
                session.flush()
            book.publisher_id = pub.id
        if auto_lock:
            lock_field(session, book, "publisher")
    if "series" in data:
        name = normalize_space(data["series"])
        if not name:
            book.series_id = None
        else:
            series = session.scalar(select(Series).where(Series.name == name))
            if not series:
                series = Series(name=name, sort_name=sort_key(name))
                session.add(series)
                session.flush()
            book.series_id = series.id
        if auto_lock:
            lock_field(session, book, "series")

    book.updated_at = datetime.now(timezone.utc)
    session.flush()
    session.refresh(book)
    return get_book_by_uuid(session, book.uuid) or book
