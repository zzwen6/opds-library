from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from app.parsers.base import ParsedBook
from app.util import find_isbn, normalize_space


def _maybe_image_from_page(page) -> tuple[bytes | None, str | None]:
    try:
        images = page.images
    except Exception:
        return None, None
    if not images:
        return None, None
    image = images[0]
    data = getattr(image, "data", None)
    name = getattr(image, "name", "") or ""
    if not data:
        return None, None
    lower = name.lower()
    mime = "image/jpeg"
    if lower.endswith(".png"):
        mime = "image/png"
    elif lower.endswith(".gif"):
        mime = "image/gif"
    return data, mime


def parse_pdf(path: Path) -> ParsedBook:
    parsed = ParsedBook()
    try:
        reader = PdfReader(str(path))
    except Exception:
        return parsed

    meta = reader.metadata or {}
    title = getattr(meta, "title", None) or (meta.get("/Title") if hasattr(meta, "get") else None)
    author = getattr(meta, "author", None) or (meta.get("/Author") if hasattr(meta, "get") else None)
    subject = getattr(meta, "subject", None) or (meta.get("/Subject") if hasattr(meta, "get") else None)
    parsed.title = normalize_space(str(title)) if title else None
    if author:
        authors = [normalize_space(part) for part in str(author).replace(";", ",").split(",")]
        parsed.authors = [a for a in authors if a]
    parsed.description = normalize_space(str(subject)) if subject else None
    parsed.page_count = len(reader.pages)

    blob = " ".join(
        filter(
            None,
            [
                parsed.title,
                author and str(author),
                parsed.description,
                str(getattr(meta, "keywords", "") or ""),
            ],
        )
    )
    parsed.isbn13, parsed.isbn10 = find_isbn(blob)
    if not parsed.isbn13 and reader.pages:
        # 只扫前两页文本，避免大文件卡住
        try:
            text = ""
            for page in reader.pages[:2]:
                text += page.extract_text() or ""
            parsed.isbn13, parsed.isbn10 = find_isbn(text)
        except Exception:
            pass

    if reader.pages:
        parsed.cover_bytes, parsed.cover_mime = _maybe_image_from_page(reader.pages[0])
    return parsed
