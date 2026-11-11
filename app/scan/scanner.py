from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Author, Book, BookAuthor, BookFile, BookTag, Cover, Publisher, Series, Tag
from app.parsers import parse_file
from app.parsers.thumbnails import save_cover_and_thumb
from app.scan.hasher import hash_file
from app.util import (
    SUPPORTED_EXTENSIONS,
    format_from_path,
    normalize_space,
    parse_filename_hint,
    should_skip_dir,
    sort_key,
)


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    moved: int = 0
    missing: int = 0
    restored: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    scanned_files: int = 0


def iter_library_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for dirpath, dirnames, filenames in root.walk() if hasattr(root, "walk") else _walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for name in filenames:
            if name.startswith("."):
                continue
            path = dirpath / name
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
    return files


def _walk(root: Path):
    for dirpath, dirnames, filenames in os_walk(root):
        yield Path(dirpath), dirnames, filenames


def os_walk(root: Path):
    import os

    yield from os.walk(root)


def _stat(path: Path) -> tuple[int, float, int | None]:
    st = path.stat()
    inode = getattr(st, "st_ino", None)
    return st.st_size, st.st_mtime, inode


def _get_or_create_author(session: Session, name: str) -> Author:
    name = normalize_space(name) or name
    author = session.scalar(select(Author).where(Author.name == name))
    if author:
        return author
    author = Author(name=name, sort_name=sort_key(name))
    session.add(author)
    session.flush()
    return author


def _get_or_create_publisher(session: Session, name: str) -> Publisher:
    name = normalize_space(name) or name
    item = session.scalar(select(Publisher).where(Publisher.name == name))
    if item:
        return item
    item = Publisher(name=name)
    session.add(item)
    session.flush()
    return item


def _get_or_create_series(session: Session, name: str) -> Series:
    name = normalize_space(name) or name
    item = session.scalar(select(Series).where(Series.name == name))
    if item:
        return item
    item = Series(name=name, sort_name=sort_key(name))
    session.add(item)
    session.flush()
    return item


def _get_or_create_tag(session: Session, name: str, kind: str = "subject") -> Tag:
    name = normalize_space(name) or name
    item = session.scalar(select(Tag).where(Tag.name == name))
    if item:
        return item
    item = Tag(name=name, kind=kind)
    session.add(item)
    session.flush()
    return item


def _attach_cover(session: Session, book: Book, data: bytes, mime: str | None, source: str) -> Cover:
    settings = get_settings()
    ext = ".png" if (mime or "").endswith("png") else ".jpg"
    rel = f"{book.uuid}{ext}"
    thumb_rel = f"{book.uuid}_thumb.jpg"
    dest = settings.covers_path / rel
    thumb = settings.covers_path / thumb_rel
    width, height, mime = save_cover_and_thumb(data, dest, thumb)
    # 取消旧主封面
    for existing in book.covers:
        existing.is_primary = False
    cover = Cover(
        book_id=book.id,
        source=source,
        rel_path=rel,
        thumb_path=thumb_rel,
        mime=mime,
        width=width,
        height=height,
        is_primary=True,
    )
    session.add(cover)
    session.flush()
    book.cover_id = cover.id
    return cover


def _fill_book_from_parsed(session: Session, book: Book, parsed, filename: str) -> None:
    title = parsed.title
    authors = list(parsed.authors)
    if not title:
        hint_title, hint_author = parse_filename_hint(filename)
        title = hint_title
        if hint_author and not authors:
            authors = [hint_author]
    book.title = title or Path(filename).stem
    book.sort_title = sort_key(book.title)
    book.subtitle = parsed.subtitle or book.subtitle
    book.isbn13 = book.isbn13 or parsed.isbn13
    book.isbn10 = book.isbn10 or parsed.isbn10
    book.language = book.language or parsed.language
    book.description = book.description or parsed.description
    book.published_date = book.published_date or parsed.published_date
    book.page_count = book.page_count or parsed.page_count
    book.series_index = book.series_index or parsed.series_index
    if parsed.publisher and not book.publisher_id:
        book.publisher = _get_or_create_publisher(session, parsed.publisher)
    if parsed.series and not book.series_id:
        book.series = _get_or_create_series(session, parsed.series)
    if authors and not book.authors:
        for i, name in enumerate(authors):
            author = _get_or_create_author(session, name)
            session.add(BookAuthor(book=book, author=author, role="author", position=i))
    if parsed.subjects and not book.tags:
        for subject in parsed.subjects:
            tag = _get_or_create_tag(session, subject)
            session.add(BookTag(book=book, tag=tag))


def _find_moved(session: Session, sha256: str, xxhash64: str, size: int, inode: int | None) -> BookFile | None:
    if sha256:
        found = session.scalar(
            select(BookFile).where(BookFile.sha256 == sha256).order_by(BookFile.id)
        )
        if found:
            return found
    if inode:
        found = session.scalar(
            select(BookFile).where(
                BookFile.inode == inode,
                BookFile.size_bytes == size,
            )
        )
        if found:
            return found
    if xxhash64:
        found = session.scalar(
            select(BookFile).where(
                BookFile.xxhash64 == xxhash64,
                BookFile.size_bytes == size,
            )
        )
        if found:
            return found
    return None


def scan_library(session: Session, root: Path | None = None) -> ScanResult:
    settings = get_settings()
    root = (root or settings.library_path).resolve()
    settings.covers_path.mkdir(parents=True, exist_ok=True)
    result = ScanResult()

    existing = {row.rel_path: row for row in session.scalars(select(BookFile)).all()}
    seen_paths: set[str] = set()

    for path in iter_library_files(root):
        result.scanned_files += 1
        try:
            rel = str(path.resolve().relative_to(root))
        except ValueError:
            rel = str(path)
        seen_paths.add(rel)
        try:
            size, mtime, inode = _stat(path)
            sha256, xxhash64 = hash_file(path)
        except OSError as exc:
            result.errors.append(f"{rel}: {exc}")
            continue

        current = existing.get(rel)
        if current and current.size_bytes == size and current.mtime == mtime and current.sha256 == sha256:
            if current.missing_at is not None:
                current.missing_at = None
                result.restored += 1
            continue

        if current is None:
            moved = _find_moved(session, sha256, xxhash64, size, inode)
            if moved and moved.rel_path not in seen_paths - {rel}:
                # 路径变了，视为移动/改名
                old = moved.rel_path
                if old in existing and old != rel:
                    existing.pop(old, None)
                moved.rel_path = rel
                moved.filename = path.name
                moved.size_bytes = size
                moved.mtime = mtime
                moved.inode = inode
                moved.sha256 = sha256
                moved.xxhash64 = xxhash64
                moved.missing_at = None
                existing[rel] = moved
                result.moved += 1
                continue
            if moved and moved.rel_path != rel and moved.rel_path in seen_paths:
                # 同哈希但原路径还在：重复副本，挂到同一本书
                dup = BookFile(
                    book_id=moved.book_id,
                    rel_path=rel,
                    filename=path.name,
                    format=format_from_path(path),
                    size_bytes=size,
                    sha256=sha256,
                    xxhash64=xxhash64,
                    mtime=mtime,
                    inode=inode,
                )
                session.add(dup)
                session.flush()
                existing[rel] = dup
                result.duplicates += 1
                continue

            parsed = parse_file(path)
            # 同 ISBN 且已有书目：作为另一格式附上
            book = None
            if parsed.isbn13:
                book = session.scalar(select(Book).where(Book.isbn13 == parsed.isbn13))
            book_file = BookFile(
                rel_path=rel,
                filename=path.name,
                format=format_from_path(path),
                size_bytes=size,
                sha256=sha256,
                xxhash64=xxhash64,
                mtime=mtime,
                inode=inode,
                extracted_title=parsed.title,
                extracted_author=", ".join(parsed.authors) if parsed.authors else None,
                extracted_isbn=parsed.isbn13 or parsed.isbn10,
            )
            if book is None:
                book = Book(title=parsed.title or path.stem, scrape_status="pending")
                session.add(book)
                session.flush()
                _fill_book_from_parsed(session, book, parsed, path.name)
                if parsed.cover_bytes:
                    try:
                        _attach_cover(session, book, parsed.cover_bytes, parsed.cover_mime, "embedded")
                    except Exception as exc:
                        result.errors.append(f"{rel} 封面: {exc}")
                result.added += 1
            else:
                book_file.book = book
                result.updated += 1
            book_file.book = book
            session.add(book_file)
            session.flush()
            existing[rel] = book_file
            continue

        # 已有路径但内容变了：重新哈希与提取
        current.size_bytes = size
        current.mtime = mtime
        current.inode = inode
        current.sha256 = sha256
        current.xxhash64 = xxhash64
        current.missing_at = None
        parsed = parse_file(path)
        current.extracted_title = parsed.title
        current.extracted_author = ", ".join(parsed.authors) if parsed.authors else None
        current.extracted_isbn = parsed.isbn13 or parsed.isbn10
        book = current.book
        locked = {lock.field_name for lock in book.locks}
        if "title" not in locked and parsed.title:
            book.title = parsed.title
            book.sort_title = sort_key(book.title)
        if "isbn13" not in locked and parsed.isbn13:
            book.isbn13 = parsed.isbn13
            book.isbn10 = parsed.isbn10
        if parsed.cover_bytes and "cover" not in locked:
            try:
                _attach_cover(session, book, parsed.cover_bytes, parsed.cover_mime, "embedded")
            except Exception as exc:
                result.errors.append(f"{rel} 封面: {exc}")
        result.updated += 1

    now = datetime.now(timezone.utc)
    for rel_path, row in list(existing.items()):
        if rel_path not in seen_paths and row.missing_at is None:
            row.missing_at = now
            result.missing += 1

    session.flush()
    return result
