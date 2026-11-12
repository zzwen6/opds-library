from __future__ import annotations

import json
import re
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
    "translators",
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
        series_index=book.series_index,
        tags=[bt.tag.name for bt in book.tags if bt.tag],
        rating=book.rating,
        published_date=book.published_date,
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
        page_count=book.page_count,
        translators=book.translators,
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


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(text: str) -> int | None:
    total, num = 0, None
    for ch in text:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (1 if num is None else num) * _CN_UNITS[ch]
            num = None
        else:
            return None
    total += num or 0
    return total if text else None


def series_index_key(value: str | None) -> tuple:
    """丛书卷号的自然排序键：数字（含 1.5）与中文数字按数值，其余按文本，空值排最后。"""
    text = normalize_space(value)
    if not text:
        return (2, 0.0, "")
    m = re.search(r"\d+(?:\.\d+)?", text)
    if m:
        return (0, float(m.group()), text.casefold())
    m = re.search(r"[零〇一二两三四五六七八九十百千]+", text)
    if m:
        n = _cn_to_int(m.group())
        if n is not None:
            return (0, float(n), text.casefold())
    return (1, 0.0, text.casefold())


def _title_key(sort_title: str | None, title: str | None) -> str:
    return (sort_title or title or "").casefold()


BOOK_SORTS = ("added", "title", "author", "series", "rating", "published")


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
    missing_cover: bool = False,
    missing_tags: bool = False,
    sort: str = "added",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Book], int]:
    conds = []
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        author_ids = select(BookAuthor.book_id).join(Author).where(Author.name.ilike(like))
        tag_ids = select(BookTag.book_id).join(Tag).where(Tag.name.ilike(like))
        series_ids = select(Series.id).where(Series.name.ilike(like))
        publisher_ids = select(Publisher.id).where(Publisher.name.ilike(like))
        conds.append(
            or_(
                Book.title.ilike(like),
                Book.subtitle.ilike(like),
                Book.isbn13.ilike(like),
                Book.isbn10.ilike(like),
                Book.description.ilike(like),
                Book.id.in_(author_ids),
                Book.id.in_(tag_ids),
                Book.series_id.in_(series_ids),
                Book.publisher_id.in_(publisher_ids),
            )
        )
    if author_id:
        conds.append(Book.id.in_(select(BookAuthor.book_id).where(BookAuthor.author_id == author_id)))
    if series_id:
        conds.append(Book.series_id == series_id)
    if tag_id:
        conds.append(Book.id.in_(select(BookTag.book_id).where(BookTag.tag_id == tag_id)))
    if publisher_id:
        conds.append(Book.publisher_id == publisher_id)
    if language:
        conds.append(Book.language == language)
    if format:
        file_ids = select(BookFile.book_id).where(BookFile.format == format, BookFile.missing_at.is_(None))
        conds.append(Book.id.in_(file_ids))
    if scrape_status:
        conds.append(Book.scrape_status == scrape_status)
    if missing_cover:
        # 与列表取封面的逻辑一致：没有主封面且没有任何封面记录
        conds.append(Book.cover_id.is_(None))
        conds.append(~Book.id.in_(select(Cover.book_id)))
    if missing_tags:
        conds.append(~Book.id.in_(select(BookTag.book_id)))

    total = session.scalar(select(func.count(Book.id)).where(*conds)) or 0
    page = max(page, 1)
    offset = (page - 1) * page_size

    if sort == "series":
        # 卷号是字符串（1、1.5、第三卷…），在 Python 里做自然排序后再分页
        keys = session.execute(
            select(Book.id, Series.sort_name, Series.name, Book.series_index, Book.sort_title, Book.title)
            .outerjoin(Series, Book.series_id == Series.id)
            .where(*conds)
        ).all()
        keys.sort(
            key=lambda r: (
                r[2] is None,
                (r[1] or r[2] or "").casefold(),
                series_index_key(r[3]),
                _title_key(r[4], r[5]),
                r[0],
            )
        )
        ids = [r[0] for r in keys[offset : offset + page_size]]
        if not ids:
            return [], int(total)
        found = {
            b.id: b
            for b in session.scalars(select(Book).options(*book_query_options()).where(Book.id.in_(ids))).unique()
        }
        return [found[i] for i in ids if i in found], int(total)

    stmt = select(Book).options(*book_query_options()).where(*conds)
    title_order = (func.lower(func.coalesce(Book.sort_title, Book.title)), Book.id)
    if sort == "title":
        stmt = stmt.order_by(*title_order)
    elif sort == "author":
        first_author = (
            select(func.lower(func.coalesce(Author.sort_name, Author.name)))
            .join(BookAuthor, BookAuthor.author_id == Author.id)
            .where(BookAuthor.book_id == Book.id)
            .order_by(BookAuthor.position, Author.id)
            .limit(1)
            .correlate(Book)
            .scalar_subquery()
        )
        stmt = stmt.order_by(first_author.is_(None), first_author, *title_order)
    elif sort == "rating":
        stmt = stmt.order_by(Book.rating.is_(None), Book.rating.desc(), *title_order)
    elif sort == "published":
        no_date = or_(Book.published_date.is_(None), Book.published_date == "")
        stmt = stmt.order_by(no_date, Book.published_date.desc(), *title_order)
    else:
        stmt = stmt.order_by(Book.added_at.desc(), Book.id.desc())

    rows = session.scalars(stmt.offset(offset).limit(page_size)).unique().all()
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
        "translators",
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
