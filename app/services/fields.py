"""字段级读写，供批量编辑、作者归并、合并重复书与撤销共用。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Book
from app.schemas import BookUpdate
from app.services.books import get_book_by_uuid, update_book

SIMPLE_FIELDS = [
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
]
LIST_FIELDS = ["authors", "tags"]
NAMED_FIELDS = ["publisher", "series"]
ALL_FIELDS = SIMPLE_FIELDS + LIST_FIELDS + NAMED_FIELDS + ["cover"]


def get_value(book: Book, field: str) -> Any:
    if field == "authors":
        return [ba.author.name for ba in sorted(book.authors, key=lambda x: x.position) if ba.author]
    if field == "tags":
        return sorted(bt.tag.name for bt in book.tags if bt.tag)
    if field == "publisher":
        return book.publisher.name if book.publisher else None
    if field == "series":
        return book.series.name if book.series else None
    if field == "cover":
        return book.cover_id
    return getattr(book, field)


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def same_value(field: str, a: Any, b: Any) -> bool:
    if field == "tags":
        return sorted(a or []) == sorted(b or [])
    if is_empty(a) and is_empty(b):
        return True
    return a == b


def set_value(session: Session, book: Book, field: str, value: Any) -> Book:
    """写入单个字段（不自动加锁），返回刷新后的 Book。"""
    if field == "cover":
        book.cover_id = value
        for cover in book.covers:
            cover.is_primary = bool(value and cover.id == value)
        session.flush()
        return get_book_by_uuid(session, book.uuid) or book
    if field == "isbn13" and not value:
        book.isbn13 = None
        session.flush()
        return get_book_by_uuid(session, book.uuid) or book
    if field == "isbn10":
        book.isbn10 = value or None
        session.flush()
        return get_book_by_uuid(session, book.uuid) or book
    update = BookUpdate(**{field: value})
    if field == "isbn13":
        # update_book 会顺带推导 isbn10，这里只写 isbn13，保持字段独立
        isbn10 = book.isbn10
        book = update_book(session, book, update, auto_lock=False)
        book.isbn10 = isbn10
        session.flush()
        return book
    return update_book(session, book, update, auto_lock=False)


def get_sources(book: Book) -> dict[str, str]:
    return json.loads(book.field_sources_json or "{}")


def set_source(book: Book, field: str, source: str | None) -> None:
    sources = get_sources(book)
    if source:
        sources[field] = source
    else:
        sources.pop(field, None)
    book.field_sources_json = json.dumps(sources, ensure_ascii=False) if sources else None


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(text: str | None) -> Any:
    return json.loads(text) if text else None
