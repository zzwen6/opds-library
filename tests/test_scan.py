from pathlib import Path
import shutil

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Book, BookFile
from app.scan import scan_library
from tests.conftest import make_epub, make_pdf


def test_full_scan_imports_epub_and_pdf(env):
    lib: Path = env["library"]
    make_epub(lib / "austen" / "pride.epub", "Pride and Prejudice", "Jane Austen", isbn="9780141439518")
    make_pdf(lib / "swift" / "proposal.pdf", "A Modest Proposal", "Jonathan Swift")
    with SessionLocal() as session:
        result = scan_library(session, lib)
        session.commit()
        books = session.query(Book).all()
        files = session.query(BookFile).all()
        pride = next(b for b in books if "Pride" in b.title)
        assert pride.isbn13 == "9780141439518"
        assert any(ba.author.name == "Jane Austen" for ba in pride.authors)
        assert pride.cover_id is not None
    assert result.scanned_files == 2
    assert result.added == 2
    assert len(books) == 2
    assert len(files) == 2
    cover_dir = env["data"] / "covers"
    assert any(cover_dir.iterdir())


def test_scan_detects_rename_by_hash(env):
    lib: Path = env["library"]
    src = make_epub(lib / "old-name.epub", "Frankenstein", "Mary Shelley", isbn="9780141439471")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        book = session.query(Book).one()
        uuid = book.uuid
        file_id = book.files[0].id
    dest = lib / "moved" / "frankenstein.epub"
    dest.parent.mkdir()
    src.rename(dest)
    with SessionLocal() as session:
        result = scan_library(session, lib)
        session.commit()
        book = session.query(Book).one()
        files = session.query(BookFile).all()
    assert result.moved == 1
    assert result.added == 0
    assert book.uuid == uuid
    assert len(files) == 1
    assert files[0].id == file_id
    assert files[0].rel_path.endswith("frankenstein.epub")


def test_scan_deduplicates_identical_copy(env):
    lib: Path = env["library"]
    src = make_epub(lib / "original.epub", "Dracula", "Bram Stoker", isbn="9780141439846")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
    shutil.copy2(src, lib / "copy.epub")
    with SessionLocal() as session:
        result = scan_library(session, lib)
        session.commit()
        books = session.query(Book).all()
        files = session.query(BookFile).all()
    assert result.duplicates == 1
    assert len(books) == 1
    assert len(files) == 2


def test_scan_marks_missing_files(env):
    lib: Path = env["library"]
    path = make_epub(lib / "temp.epub", "The Time Machine", "H. G. Wells")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
    path.unlink()
    with SessionLocal() as session:
        result = scan_library(session, lib)
        session.commit()
        book_file = session.query(BookFile).one()
    assert result.missing == 1
    assert book_file.missing_at is not None
