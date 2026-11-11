from __future__ import annotations

import json

from app.config import reset_settings_cache
from app.db import SessionLocal
from app.models import Book, ScrapeRecord
from app.scan import scan_library
from app.scrapers.base import Candidate
from app.scrapers.service import scrape_book, scrape_pending_books, undo_auto_apply
from app.services.books import BookUpdate, get_book_by_uuid, update_book
from tests.conftest import make_epub


class _FakeScraper:
    id = "fake"
    display_name = "Fake"

    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates

    def lookup_isbn(self, isbn: str) -> list[Candidate]:  # noqa: ARG002
        return list(self._candidates)

    def search(self, title, author=None, language=None):  # noqa: ARG002
        return list(self._candidates)


def _install_fake(monkeypatch, candidates: list[Candidate]):
    def factory():
        class Wrapper(_FakeScraper):
            def __init__(self):
                super().__init__(candidates)

        Wrapper.id = "fake"
        return Wrapper

    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [factory()])


def _scan_one(library, title="Original Title", author="Original Author", isbn=None) -> str:
    make_epub(library / "book.epub", title, author, isbn=isbn)
    with SessionLocal() as session:
        scan_library(session, library)
        session.commit()
        return session.query(Book).one().uuid


def test_auto_apply_high_score(env, monkeypatch):
    uuid = _scan_one(env["library"])
    _install_fake(
        monkeypatch,
        [
            Candidate(
                source="fake",
                source_id="1",
                title="Original Title",
                authors=["Original Author"],
                description="网络简介",
                publisher="新出版社",
                confidence=95,
            )
        ],
    )
    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        scrape_book(session, book)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "Original Title"
        assert book.description == "网络简介"
        assert book.publisher is not None and book.publisher.name == "新出版社"
        assert book.scrape_status == "auto_applied"
        sources = json.loads(book.field_sources_json or "{}")
        assert sources["description"] == "fake"
        rec = next(r for r in book.scrape_records if r.status == "auto_applied")
        snap = json.loads(rec.previous_values_json)
        assert snap["previous"]["description"] in (None, "")
        assert rec.applied_mode == "auto"


def test_below_threshold_goes_to_review(env, monkeypatch):
    uuid = _scan_one(env["library"])
    _install_fake(
        monkeypatch,
        [
            Candidate(
                source="fake",
                source_id="1",
                title="Maybe Title",
                authors=["Someone"],
                description="low",
                confidence=70,
            )
        ],
    )
    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        scrape_book(session, book)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "Original Title"
        assert book.description is None or book.description != "low"
        assert book.scrape_status == "needs_review"


def test_isbn_exact_auto_applies_even_if_score_low(env, monkeypatch):
    isbn = "9780141439518"
    uuid = _scan_one(env["library"], isbn=isbn)
    _install_fake(
        monkeypatch,
        [
            Candidate(
                source="fake",
                source_id="1",
                title="Different Title",
                isbn13=isbn,
                description="ISBN hit",
                confidence=40,
            )
        ],
    )
    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        assert book.isbn13 == isbn
        scrape_book(session, book)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "Different Title"
        assert book.description == "ISBN hit"
        assert book.scrape_status == "auto_applied"


def test_auto_apply_respects_locks(env, monkeypatch):
    uuid = _scan_one(env["library"])
    _install_fake(
        monkeypatch,
        [
            Candidate(
                source="fake",
                source_id="1",
                title="手改标题",
                authors=["Original Author"],
                description="来自网络的简介",
                publisher="网络出版社",
                confidence=99,
            )
        ],
    )
    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        update_book(session, book, BookUpdate(title="手改标题"), auto_lock=True)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        scrape_book(session, book)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "手改标题"
        assert book.description == "来自网络的简介"
        authors = [ba.author.name for ba in book.authors]
        assert "Original Author" in authors
        assert book.scrape_status == "auto_applied"
        sources = json.loads(book.field_sources_json or "{}")
        assert "title" not in sources
        assert sources["description"] == "fake"


def test_all_locked_stays_needs_review(env, monkeypatch):
    uuid = _scan_one(env["library"])
    _install_fake(
        monkeypatch,
        [
            Candidate(
                source="fake",
                source_id="1",
                title="刮削标题",
                authors=["Network Author"],
                description="简介",
                confidence=99,
            )
        ],
    )
    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        update_book(
            session,
            book,
            BookUpdate(title="手改", authors=["手改作者"], description="手改简介"),
            auto_lock=True,
        )
        session.commit()
        book = get_book_by_uuid(session, uuid)
        scrape_book(session, book)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "手改"
        assert book.description == "手改简介"
        assert book.scrape_status == "needs_review"
        recs = [r for r in book.scrape_records if r.status == "auto_applied"]
        assert recs == []


def test_undo_restores_previous_values(env, monkeypatch):
    uuid = _scan_one(env["library"])
    _install_fake(
        monkeypatch,
        [
            Candidate(
                source="fake",
                source_id="1",
                title="Original Title",
                authors=["Original Author"],
                description="网络简介",
                publisher="新出版社",
                confidence=95,
            )
        ],
    )
    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        scrape_book(session, book)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.description == "网络简介"
        assert book.publisher is not None and book.publisher.name == "新出版社"
        book = undo_auto_apply(session, book)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "Original Title"
        authors = [ba.author.name for ba in book.authors]
        assert "Original Author" in authors
        assert book.description in (None, "")
        assert book.scrape_status == "needs_review"
        assert not (book.field_sources_json)
        recs = [r for r in book.scrape_records if r.status == "auto_undone"]
        assert recs


def test_batch_only_pending(env, monkeypatch):
    lib = env["library"]
    make_epub(lib / "a.epub", "Book A", "Author A")
    make_epub(lib / "b.epub", "Book B", "Author B")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        books = list(session.query(Book).all())
        books[0].scrape_status = "needs_review"
        session.commit()
        kept = books[0].title

    class _Echo:
        id = "fake"

        def lookup_isbn(self, isbn):
            return []

        def search(self, title, author=None, language=None):
            return [
                Candidate(
                    source="fake",
                    source_id="echo",
                    title=title,
                    authors=[author or "Author"],
                    description="批量简介",
                    publisher="批量出版社",
                )
            ]

    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [_Echo])
    with SessionLocal() as session:
        result = scrape_pending_books(session)
        session.commit()
        assert result["total"] == 1
        assert result["auto_applied"] == 1
        books = list(session.query(Book).all())
        by_title = {b.title: b for b in books}
        assert by_title[kept].scrape_status == "needs_review"
        pending = [b for b in books if b.title != kept][0]
        assert pending.scrape_status == "auto_applied"
        assert pending.description == "批量简介"
