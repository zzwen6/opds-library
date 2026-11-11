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


def make_mobi(path: Path, title: str, author: str) -> Path:
    """生成一个能被 mobi/KindleUnpack 解开的最小 MOBI（PalmDB + MOBI 头 + EXTH，正文不压缩）。"""
    import struct

    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"<html><head></head><body><p>{title}</p><p>测试正文。</p></body></html>".encode()
    title_b = title.encode()
    exth_recs = [(100, author.encode()), (503, title_b)]
    body = b"".join(struct.pack(">II", t, len(d) + 8) + d for t, d in exth_recs)
    exth_len = 12 + len(body)
    pad = (4 - exth_len % 4) % 4
    exth = b"EXTH" + struct.pack(">II", exth_len + pad, len(exth_recs)) + body + b"\0" * pad
    mobi_len = 0xE8
    full_name_off = 16 + mobi_len + len(exth)
    mh = bytearray(mobi_len)

    def put(record_offset: int, value: int, fmt: str = ">I") -> None:
        # record_offset 为相对 record 0 起点的偏移（含 16 字节 PalmDOC 头）
        struct.pack_into(fmt, mh, record_offset - 16, value)

    mh[0:4] = b"MOBI"
    put(0x14, mobi_len)
    put(0x18, 2)  # 书籍
    put(0x1C, 65001)  # UTF-8
    put(0x20, 12345)
    put(0x24, 6)  # 文件版本
    for off in range(0x28, 0x50, 4):
        put(off, 0xFFFFFFFF)
    put(0x50, 2)  # first non-book record
    put(0x54, full_name_off)
    put(0x58, len(title_b))
    put(0x5C, 4)  # locale: zh
    put(0x68, 6)
    put(0x6C, 0xFFFFFFFF)  # 无图片
    put(0x80, 0x40)  # 有 EXTH
    put(0xA8, 0xFFFFFFFF)  # 无 DRM
    put(0xC0, 1, ">H")
    put(0xC2, 1, ">H")
    put(0xC4, 1)
    put(0xC8, 0xFFFFFFFF)  # FCIS
    put(0xD0, 0xFFFFFFFF)  # FLIS
    put(0xF2, 0, ">H")  # extra data flags
    put(0xF4, 0xFFFFFFFF)  # 无 NCX 索引
    palmdoc = struct.pack(">HHIHHHH", 1, 0, len(text), 1, 4096, 0, 0)
    rec0 = palmdoc + bytes(mh) + exth + title_b + b"\0" * (8 - len(title_b) % 4)
    records = [rec0, text, b"\xe9\x8e\r\n"]
    n = len(records)
    hdr = b"test".ljust(32, b"\0") + struct.pack(
        ">HHIIIIII4s4sIIH", 0, 0, 0, 0, 0, 0, 0, 0, b"BOOK", b"MOBI", n, 0, n
    )
    offset = 78 + 8 * n + 2
    reclist = b""
    for i, rec in enumerate(records):
        reclist += struct.pack(">II", offset, i * 2)
        offset += len(rec)
    path.write_bytes(hdr + reclist + b"\0\0" + b"".join(records))
    return path
