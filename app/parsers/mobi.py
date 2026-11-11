from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.parsers.base import ParsedBook
from app.util import find_isbn, normalize_space


def parse_mobi(path: Path) -> ParsedBook:
    """尽最大努力解析 MOBI/AZW3。失败时返回空结果，不抛给扫描循环。"""
    parsed = ParsedBook()
    try:
        import mobi
    except Exception:
        return parsed

    tmp = tempfile.mkdtemp(prefix="mobi-")
    try:
        extracted = mobi.extract(str(path), tmp)
        # mobi.extract 返回 (tempdir, filepath) 或类似结构，版本间有差异
        root = Path(tmp)
        if isinstance(extracted, (tuple, list)) and extracted:
            maybe = Path(extracted[0] if not str(extracted[0]).endswith((".epub", ".html")) else extracted[-1])
            if maybe.exists():
                root = maybe if maybe.is_dir() else maybe.parent
        # 若解出 EPUB，复用 EPUB 解析
        epubs = list(root.rglob("*.epub"))
        if epubs:
            from app.parsers.epub import parse_epub

            return parse_epub(epubs[0])
        # 否则读 html / opf
        opf = next(root.rglob("*.opf"), None)
        if opf:
            try:
                from lxml import etree

                tree = etree.parse(str(opf))
                ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                title = tree.findtext(".//dc:title", namespaces=ns)
                creators = [el.text for el in tree.findall(".//dc:creator", namespaces=ns) if el.text]
                ident = " ".join(el.text or "" for el in tree.findall(".//dc:identifier", namespaces=ns))
                lang = tree.findtext(".//dc:language", namespaces=ns)
                desc = tree.findtext(".//dc:description", namespaces=ns)
                parsed.title = normalize_space(title)
                parsed.authors = [a for a in (normalize_space(x) for x in creators) if a]
                parsed.language = normalize_space(lang)
                parsed.description = normalize_space(desc)
                parsed.isbn13, parsed.isbn10 = find_isbn(ident)
            except Exception:
                pass
        # 封面：目录中常见 cover.jpg
        for candidate in list(root.rglob("cover.*")) + list(root.rglob("*cover*.jpg")):
            if candidate.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif"}:
                parsed.cover_bytes = candidate.read_bytes()
                parsed.cover_mime = {
                    ".png": "image/png",
                    ".gif": "image/gif",
                }.get(candidate.suffix.lower(), "image/jpeg")
                break
    except Exception:
        return parsed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return parsed
