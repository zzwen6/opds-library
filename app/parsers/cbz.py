from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from app.parsers.base import ParsedBook
from app.util import normalize_space

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _text(el, name: str) -> str | None:
    found = el.find(name)
    if found is not None and found.text:
        return normalize_space(found.text)
    return None


def parse_cbz(path: Path) -> ParsedBook:
    parsed = ParsedBook()
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            info_name = next((n for n in names if n.lower().endswith("comicinfo.xml")), None)
            if info_name:
                try:
                    root = etree.fromstring(zf.read(info_name))
                    parsed.title = _text(root, "Title") or _text(root, "title")
                    series = _text(root, "Series") or _text(root, "series")
                    parsed.series = series
                    parsed.series_index = _text(root, "Number") or _text(root, "number")
                    writers = []
                    for tag in ("Writer", "Penciller", "Inker", "Author"):
                        value = _text(root, tag)
                        if value:
                            writers.append(value)
                    parsed.authors = writers
                    parsed.published_date = _text(root, "Year") or _text(root, "year")
                    parsed.description = _text(root, "Summary") or _text(root, "summary")
                    parsed.language = _text(root, "LanguageISO") or _text(root, "language")
                    parsed.page_count = (
                        int(_text(root, "PageCount") or 0) or None
                    )
                except Exception:
                    pass
            image_names = sorted(
                n for n in names if Path(n).suffix.lower() in IMAGE_EXTS and not n.endswith("/")
            )
            parsed.page_count = parsed.page_count or len(image_names)
            if image_names:
                first = image_names[0]
                parsed.cover_bytes = zf.read(first)
                ext = Path(first).suffix.lower()
                parsed.cover_mime = {
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(ext, "image/jpeg")
    except Exception:
        return parsed
    if not parsed.title:
        parsed.title = normalize_space(path.stem)
    return parsed
