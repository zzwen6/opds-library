from __future__ import annotations

import re

from app.models import Book
from app.scrapers.base import Candidate
from app.util import normalize_isbn

# 汉字、扩展汉字、假名、韩文音节（中文书判定用；韩/日书名极少误伤 ISBN 978-7）
_CJK_RE = re.compile(r"[\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]")

_ZH_SOURCE_ORDER = {"douban": 0, "openlibrary": 1, "google_books": 2}
_DEFAULT_SOURCE_ORDER = {"openlibrary": 0, "google_books": 1, "douban": 2}


def is_chinese_book(
    book: Book | None = None,
    *,
    title: str | None = None,
    isbn13: str | None = None,
    authors: list[str] | None = None,
) -> bool:
    """中文书：ISBN 为 978-7，或书名/副标题/作者含中日韩表意文字。"""
    raw_isbn = isbn13
    if raw_isbn is None and book is not None:
        raw_isbn = book.isbn13
    isbn, _ = normalize_isbn(raw_isbn)
    if isbn and isbn.startswith("9787"):
        return True
    parts: list[str] = []
    if title:
        parts.append(title)
    elif book is not None and book.title:
        parts.append(book.title)
    if book is not None and book.subtitle:
        parts.append(book.subtitle)
    if authors:
        parts.extend(authors)
    elif book is not None:
        parts.extend(ba.author.name for ba in book.authors if ba.author)
    return any(_CJK_RE.search(part or "") for part in parts)


def source_rank(source: str, chinese: bool) -> int:
    order = _ZH_SOURCE_ORDER if chinese else _DEFAULT_SOURCE_ORDER
    return order.get(source or "", 99)


def sort_candidates(candidates: list[Candidate], book: Book) -> list[Candidate]:
    chinese = is_chinese_book(book)
    return sorted(
        candidates,
        key=lambda c: (-(c.confidence or 0), source_rank(c.source, chinese)),
    )
