from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.util import format_from_path


@dataclass
class ParsedBook:
    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = field(default_factory=list)
    isbn13: str | None = None
    isbn10: str | None = None
    language: str | None = None
    description: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    series: str | None = None
    series_index: str | None = None
    subjects: list[str] = field(default_factory=list)
    page_count: int | None = None
    cover_bytes: bytes | None = None
    cover_mime: str | None = None
    source: str = "embedded"


def parse_file(path: Path) -> ParsedBook:
    fmt = format_from_path(path)
    if fmt == "epub":
        from app.parsers.epub import parse_epub

        return parse_epub(path)
    if fmt == "pdf":
        from app.parsers.pdf import parse_pdf

        return parse_pdf(path)
    if fmt in {"mobi", "azw3"}:
        from app.parsers.mobi import parse_mobi

        return parse_mobi(path)
    if fmt == "cbz":
        from app.parsers.cbz import parse_cbz

        return parse_cbz(path)
    return ParsedBook()
