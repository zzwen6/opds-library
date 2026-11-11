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
        from mobi.kindleunpack import unpackBook
    except Exception:
        return parsed

    # 0.5.1 及之前调用 mobi.extract(path, tmp) 多传了一个参数，抛 TypeError 后被吞掉，
    # 导致 MOBI/AZW3 的内嵌元数据从未被读取。这里直接调 KindleUnpack 解到自己的临时目录，
    # 解析失败时也能清理干净（mobi.extract 失败时会遗留 /tmp/mobiex*）。
    tmp = tempfile.mkdtemp(prefix="opds-mobi-")
    try:
        unpackBook(str(path), tmp, epubver="A")
        root = Path(tmp)
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
