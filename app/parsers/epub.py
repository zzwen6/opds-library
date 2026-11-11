from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from app.parsers.base import ParsedBook
from app.util import find_isbn, normalize_space

DC = "http://purl.org/dc/elements/1.1/"
OPF = "http://www.idpf.org/2007/opf"


def _text(el) -> str | None:
    if el is None or el.text is None:
        return None
    return normalize_space(el.text)


def parse_epub_opf(path: Path) -> ParsedBook:
    parsed = ParsedBook()
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            container = zf.read("META-INF/container.xml")
            root = etree.fromstring(container)
            ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
            rootfile = root.find(".//c:rootfile", ns)
            if rootfile is None:
                opf_name = next((n for n in names if n.endswith(".opf")), None)
            else:
                opf_name = rootfile.get("full-path")
            if not opf_name:
                return parsed
            opf_dir = str(Path(opf_name).parent)
            if opf_dir == ".":
                opf_dir = ""
            tree = etree.fromstring(zf.read(opf_name))
            nsmap = {"dc": DC, "opf": OPF}

            def dc(tag: str) -> list[str]:
                values = []
                for el in tree.findall(f".//{{{DC}}}{tag}"):
                    if el.text:
                        values.append(el.text)
                return values

            titles = [normalize_space(x) for x in dc("title")]
            titles = [t for t in titles if t]
            parsed.title = titles[0] if titles else None
            if len(titles) > 1:
                parsed.subtitle = titles[1]
            parsed.authors = [a for a in (normalize_space(x) for x in dc("creator")) if a]
            parsed.language = normalize_space(dc("language")[0]) if dc("language") else None
            parsed.description = normalize_space(dc("description")[0]) if dc("description") else None
            parsed.publisher = normalize_space(dc("publisher")[0]) if dc("publisher") else None
            parsed.published_date = normalize_space(dc("date")[0]) if dc("date") else None
            parsed.subjects = [s for s in (normalize_space(x) for x in dc("subject")) if s]
            ident = " ".join(dc("identifier"))
            parsed.isbn13, parsed.isbn10 = find_isbn(ident)

            cover_href = None
            cover_id = None
            for meta in tree.findall(".//{http://www.idpf.org/2007/opf}meta") + tree.findall(".//meta"):
                name = (meta.get("name") or meta.get("property") or "").lower()
                if name in {"cover", "cover-image"} or (meta.get("property") == "cover-image"):
                    cover_id = meta.get("content") or (meta.text and meta.text.strip())
            for item in tree.findall(".//{http://www.idpf.org/2007/opf}item") + tree.findall(".//item"):
                props = (item.get("properties") or "") + " " + (item.get("id") or "")
                href = item.get("href")
                if not href:
                    continue
                if "cover-image" in props or item.get("id") == cover_id or (item.get("id") or "").lower() == "cover":
                    cover_href = href
                    break
                if cover_href is None and "cover" in href.lower() and (item.get("media-type") or "").startswith("image/"):
                    cover_href = href
            if cover_href:
                cover_path = str(Path(opf_dir, cover_href).as_posix()) if opf_dir else cover_href
                # 有些 epub 用相对路径
                candidates = [cover_path, cover_href, f"{opf_dir}/{cover_href}".replace("//", "/")]
                for cand in candidates:
                    cand = cand.lstrip("./")
                    if cand in zf.namelist():
                        parsed.cover_bytes = zf.read(cand)
                        lower = cand.lower()
                        parsed.cover_mime = "image/png" if lower.endswith(".png") else "image/jpeg"
                        break
    except Exception:
        return parsed
    return parsed


def parse_epub(path: Path) -> ParsedBook:
    parsed = parse_epub_opf(path)
    if parsed.title or parsed.cover_bytes:
        return parsed
    # 回退 ebooklib（部分复杂包）
    try:
        from ebooklib import epub, ITEM_COVER, ITEM_IMAGE

        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    except Exception:
        return parsed

    def _meta(name: str) -> list[str]:
        values = []
        for item in book.get_metadata("DC", name):
            if item and item[0]:
                values.append(str(item[0]))
        return values

    titles = _meta("title")
    parsed.title = normalize_space(titles[0]) if titles else None
    parsed.authors = [a for a in (normalize_space(x) for x in _meta("creator")) if a]
    parsed.language = normalize_space(_meta("language")[0]) if _meta("language") else None
    parsed.description = normalize_space(_meta("description")[0]) if _meta("description") else None
    parsed.publisher = normalize_space(_meta("publisher")[0]) if _meta("publisher") else None
    ident = " ".join(_meta("identifier"))
    parsed.isbn13, parsed.isbn10 = find_isbn(ident)
    parsed.subjects = [s for s in (normalize_space(x) for x in _meta("subject")) if s]
    try:
        for candidate in list(book.get_items_of_type(ITEM_COVER)) + list(book.get_items_of_type(ITEM_IMAGE)):
            data = candidate.get_content()
            if data:
                parsed.cover_bytes = data
                parsed.cover_mime = getattr(candidate, "media_type", None) or "image/jpeg"
                break
    except Exception:
        pass
    return parsed
