"""网页上传图书：写入书库 inbox/，扫描入库，并在后台自动刮削。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import BinaryIO, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import BookFile
from app.scan import scan_library
from app.scrapers.service import scrape_book
from app.services.books import get_book_by_uuid
from app.services.organize import _unique_dest, library_is_writable, sanitize_component
from app.util import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

INBOX_DIR = "inbox"
MAX_UPLOAD_BYTES = 1 * 1024 * 1024 * 1024  # 单文件 1 GiB
MAX_UPLOAD_FILES = 50
CHUNK_SIZE = 1024 * 1024

LIBRARY_NOT_WRITABLE = (
    "书库目录不可写。请将 docker-compose 中 /library 的挂载去掉 :ro 后重新部署。"
    "上传图书不需要开启 ORGANIZE_ENABLED。"
)

_scrape_lock = threading.Lock()
_scrape_threads: list[threading.Thread] = []


def format_list_text() -> str:
    names = sorted({ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS})
    return "、".join(names)


def sanitize_upload_filename(original: str | None) -> str:
    """去掉路径，保留可读的中文名，并替换非法字符。"""
    raw = (original or "").replace("\\", "/").replace("\x00", "")
    name = Path(raw).name
    if name in {"", ".", ".."}:
        name = "未命名"
    suffix = Path(name).suffix.lower()
    stem = Path(name).stem or "未命名"
    clean = sanitize_component(stem, "未命名")
    if suffix and suffix not in SUPPORTED_EXTENSIONS:
        # 仍拼上原后缀，由调用方按扩展名拒绝
        return f"{clean}{suffix}"
    return f"{clean}{suffix}"


def _unique_inbox_rel(root: Path, filename: str, reserved: set[str]) -> str:
    rel = f"{INBOX_DIR}/{filename}"
    dummy = root / ".upload-src-unused"
    final, _note = _unique_dest(root, rel, dummy, reserved)
    return final


def _write_stream(dest: Path, stream: BinaryIO, max_bytes: int) -> int:
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError(
                        f"文件过大，单个文件不能超过 {max_bytes // (1024 * 1024)} MB"
                    )
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if written == 0:
        dest.unlink(missing_ok=True)
        raise ValueError("文件是空的，无法上传")
    return written


def _duplicate_info(session: Session, book_file: BookFile) -> dict:
    others = [
        f
        for f in session.scalars(
            select(BookFile).where(
                BookFile.book_id == book_file.book_id,
                BookFile.id != book_file.id,
                BookFile.missing_at.is_(None),
            )
        ).all()
    ]
    if not others:
        # 也可能哈希相同但挂在检测逻辑尚未关联的书上（少见）；再按哈希查一次
        if book_file.sha256:
            hashed = session.scalars(
                select(BookFile).where(
                    BookFile.sha256 == book_file.sha256,
                    BookFile.id != book_file.id,
                    BookFile.missing_at.is_(None),
                )
            ).all()
            if hashed:
                other = hashed[0]
                book = other.book
                return {
                    "possible_duplicate": True,
                    "duplicate_reason": "库中已有内容完全相同的副本",
                    "duplicate_of": {
                        "uuid": book.uuid if book else None,
                        "title": book.title if book else None,
                    },
                }
        return {"possible_duplicate": False, "duplicate_reason": None, "duplicate_of": None}

    same_hash = any(f.sha256 and f.sha256 == book_file.sha256 for f in others)
    book = book_file.book
    if same_hash:
        reason = "库中已有内容完全相同的副本"
    else:
        reason = "已作为已有书目的另一份文件入库"
    return {
        "possible_duplicate": True,
        "duplicate_reason": reason,
        "duplicate_of": {
            "uuid": book.uuid if book else None,
            "title": book.title if book else None,
        },
    }


def _item_error(original: str | None, filename: str | None, error: str) -> dict:
    return {
        "ok": False,
        "original_filename": original,
        "filename": filename,
        "rel_path": None,
        "book_uuid": None,
        "title": None,
        "scrape_status": None,
        "possible_duplicate": False,
        "duplicate_reason": None,
        "duplicate_of": None,
        "error": error,
    }


def _item_ok(original: str | None, filename: str, rel_path: str, book, dup: dict) -> dict:
    return {
        "ok": True,
        "original_filename": original,
        "filename": filename,
        "rel_path": rel_path,
        "book_uuid": book.uuid if book else None,
        "title": book.title if book else None,
        "scrape_status": book.scrape_status if book else None,
        "possible_duplicate": dup.get("possible_duplicate", False),
        "duplicate_reason": dup.get("duplicate_reason"),
        "duplicate_of": dup.get("duplicate_of"),
        "error": None,
    }


def save_uploads(
    session: Session,
    uploads: Iterable[tuple[str | None, BinaryIO]],
    *,
    max_bytes: int | None = None,
) -> dict:
    """将多个上传文件写入 inbox/，扫描入库。不在此函数内刮削。"""
    settings = get_settings()
    root = settings.library_path.resolve()
    if not library_is_writable(root):
        raise PermissionError(LIBRARY_NOT_WRITABLE)

    max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    inbox = root / INBOX_DIR
    inbox.mkdir(parents=True, exist_ok=True)

    uploads = list(uploads)
    if len(uploads) > MAX_UPLOAD_FILES:
        raise ValueError(f"一次最多上传 {MAX_UPLOAD_FILES} 个文件")

    reserved: set[str] = set()
    written: list[dict] = []
    items: list[dict] = []

    for original, stream in uploads:
        filename = sanitize_upload_filename(original)
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            items.append(
                _item_error(
                    original,
                    filename,
                    f"不支持的文件格式，仅接受 {format_list_text()}",
                )
            )
            continue
        try:
            rel = _unique_inbox_rel(root, filename, reserved)
            dest = (root / rel).resolve()
            dest.relative_to(root)
        except ValueError as exc:
            items.append(_item_error(original, filename, f"非法文件名：{exc}"))
            continue
        try:
            _write_stream(dest, stream, max_bytes)
        except ValueError as exc:
            items.append(_item_error(original, filename, str(exc)))
            continue
        except OSError as exc:
            items.append(_item_error(original, Path(rel).name, f"保存失败：{exc}"))
            continue
        written.append({"original": original, "filename": Path(rel).name, "rel_path": rel})

    if written:
        scan_library(session, root)
        session.flush()

    scrape_uuids: list[str] = []
    seen_scrape: set[str] = set()

    for w in written:
        book_file = session.scalar(select(BookFile).where(BookFile.rel_path == w["rel_path"]))
        if book_file is None:
            items.append(
                _item_error(w["original"], w["filename"], "文件已保存，但扫描后未入库")
            )
            continue
        book = book_file.book
        dup = _duplicate_info(session, book_file)
        items.append(_item_ok(w["original"], w["filename"], w["rel_path"], book, dup))
        if book and book.scrape_status == "pending" and book.uuid not in seen_scrape:
            seen_scrape.add(book.uuid)
            scrape_uuids.append(book.uuid)

    saved = sum(1 for i in items if i["ok"])
    return {
        "ok": saved > 0,
        "saved": saved,
        "failed": sum(1 for i in items if not i["ok"]),
        "items": items,
        "scrape_uuids": scrape_uuids,
    }


def start_upload_scrape(book_uuids: list[str]) -> None:
    if not book_uuids:
        return
    thread = threading.Thread(
        target=_run_upload_scrape, args=(list(book_uuids),), daemon=True, name="upload-scrape"
    )
    thread.start()
    with _scrape_lock:
        _scrape_threads.append(thread)


def wait_upload_scrape(timeout: float = 20.0) -> None:
    """供测试等待后台刮削结束。"""
    with _scrape_lock:
        threads = list(_scrape_threads)
    for thread in threads:
        thread.join(timeout)


def _run_upload_scrape(uuids: list[str]) -> None:
    session = SessionLocal()
    try:
        for uuid in uuids:
            book = get_book_by_uuid(session, uuid)
            if not book:
                continue
            try:
                scrape_book(session, book)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("上传后自动刮削失败 %s", uuid)
    finally:
        session.close()
