from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import reset_settings_cache
from app.db import Base, ensure_data_dirs, init_engine
from app.util import normalize_isbn


@pytest.fixture
def env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    data = tmp_path / "data"
    library.mkdir()
    data.mkdir()
    monkeypatch.setenv("LIBRARY_PATH", str(library))
    monkeypatch.setenv("DATA_PATH", str(data))
    monkeypatch.setenv("AUTH_USERNAME", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "secret")
    monkeypatch.setenv("PAGE_SIZE", "2")
    monkeypatch.setenv("WATCH_ENABLED", "false")
    monkeypatch.setenv("ORGANIZE_ENABLED", "false")
    monkeypatch.setenv("DOUBAN_ENABLED", "false")
    monkeypatch.setenv("DOUBAN_MIN_INTERVAL_SECONDS", "0")
    reset_settings_cache()
    from app.config import get_settings

    settings = get_settings()
    ensure_data_dirs()
    engine = init_engine(settings.database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from app.main import create_app

    app = create_app()
    yield {"app": app, "library": library, "data": data, "settings": settings, "engine": engine}
    reset_settings_cache()


@pytest.fixture
def client(env) -> TestClient:
    return TestClient(env["app"])


@pytest.fixture
def auth() -> tuple[str, str]:
    return ("admin", "secret")


def make_cover_bytes(color: tuple[int, int, int] = (140, 40, 30)) -> bytes:
    img = Image.new("RGB", (200, 300), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_epub(
    path: Path,
    title: str,
    author: str,
    isbn: str | None = None,
    language: str = "en",
    description: str | None = None,
    subject: str | None = None,
    cover: bytes | None = None,
) -> Path:
    import zipfile
    from xml.sax.saxutils import escape

    isbn13, _ = normalize_isbn(isbn) if isbn else (None, None)
    ident = isbn13 or f"urn:uuid:{path.stem}"
    if cover is None:
        cover = make_cover_bytes()
    desc = f"<dc:description>{escape(description)}</dc:description>" if description else ""
    subj = f"<dc:subject>{escape(subject)}</dc:subject>" if subject else ""
    isbn_meta = f'<dc:identifier id="isbn">urn:isbn:{escape(isbn13)}</dc:identifier>' if isbn13 else ""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="BookId">{escape(ident)}</dc:identifier>
    {isbn_meta}
    <dc:title>{escape(title)}</dc:title>
    <dc:creator opf:role="aut">{escape(author)}</dc:creator>
    <dc:language>{escape(language)}</dc:language>
    {desc}
    {subj}
    <meta name="cover" content="cover"/>
  </metadata>
  <manifest>
    <item id="chap" href="chap.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chap"/>
  </spine>
</package>
"""
    chap = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{escape(title)}</title></head>
<body><h1>{escape(title)}</h1><p>Public domain test fixture.</p></body></html>
"""
    ncx = f"""<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{escape(ident)}"/></head>
  <docTitle><text>{escape(title)}</text></docTitle>
  <navMap><navPoint id="n1"><navLabel><text>{escape(title)}</text></navLabel><content src="chap.xhtml"/></navPoint></navMap>
</ncx>
"""
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("EPUB/content.opf", opf)
        zf.writestr("EPUB/chap.xhtml", chap)
        zf.writestr("EPUB/toc.ncx", ncx)
        zf.writestr("EPUB/cover.jpg", cover)
    return path


def make_pdf(path: Path, title: str, author: str, isbn: str | None = None) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/Title": title, "/Author": author, "/Subject": isbn or title})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def make_cbz(path: Path, title: str, series: str = "Test Series") -> Path:
    import zipfile

    xml = f"""<?xml version="1.0"?>
<ComicInfo>
  <Title>{title}</Title>
  <Series>{series}</Series>
  <Number>1</Number>
  <Writer>Test Author</Writer>
  <Year>2020</Year>
  <Summary>A fixture comic.</Summary>
</ComicInfo>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", xml)
        zf.writestr("page1.jpg", make_cover_bytes((20, 80, 60)))
    return path
