"""清理脏标题并重新刮削。"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import Book
from app.scrapers.base import Candidate
from app.scrapers.service import (
    clean_unlocked_dirty_titles,
    scrape_pending_books,
    scrape_rescrape_books,
)
from app.services.books import lock_field


class _RecordingScraper:
    id = "fake"
    display_name = "Fake"
    queries: list[str] = []
    answer_for: str = "三体"

    def lookup_isbn(self, isbn):  # noqa: ARG002
        return []

    def search(self, title, author=None, language=None):  # noqa: ARG002
        type(self).queries.append(title)
        if title == type(self).answer_for:
            return [Candidate(source="fake", source_id="2567698", title="三体", authors=["刘慈欣"], language="zh")]
        return []


def _book(**kwargs) -> str:
    kwargs.setdefault("scrape_status", "pending")
    kwargs.setdefault("title", "未命名")
    with SessionLocal() as s:
        book = Book(**kwargs)
        s.add(book)
        s.commit()
        return book.uuid


def test_clean_unlocked_dirty_titles_skips_locks(env):
    dirty = _book(title="Kindle电子书:《三体》")
    locked = _book(title="Kindle电子书:《活着》")
    clean = _book(title="围城")
    with SessionLocal() as s:
        book = s.query(Book).filter(Book.uuid == locked).one()
        lock_field(s, book, "title")
        s.commit()
        result = clean_unlocked_dirty_titles(s)
        s.commit()
        by_uuid = {c["uuid"]: c for c in result}
        assert dirty in by_uuid
        assert by_uuid[dirty]["title"] == "三体"
        assert by_uuid[dirty]["old_title"] == "Kindle电子书:《三体》"
        assert locked not in by_uuid
        assert clean not in by_uuid
        assert s.query(Book).filter(Book.uuid == locked).one().title == "Kindle电子书:《活着》"
        assert s.query(Book).filter(Book.uuid == clean).one().title == "围城"
        # 从书名号外解析到作者并补上
        book = s.query(Book).filter(Book.uuid == dirty).one()
        # 这个书名没有作者提示
        assert [ba.author.name for ba in book.authors] == []


def test_clean_fills_author_hint(env):
    uuid = _book(title="刘慈欣《三体》")
    with SessionLocal() as s:
        result = clean_unlocked_dirty_titles(s)
        s.commit()
        assert result[0]["title"] == "三体"
        assert result[0]["author_set"] == "刘慈欣"
        book = s.query(Book).filter(Book.uuid == uuid).one()
        assert book.title == "三体"
        assert [ba.author.name for ba in book.authors] == ["刘慈欣"]


def test_pending_batch_does_not_touch_failed(env, monkeypatch):
    _RecordingScraper.queries = []
    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [_RecordingScraper])
    from app.services.books import BookUpdate, get_book_by_uuid, update_book

    pending = _book(title="三体", scrape_status="pending")
    failed = _book(title="三体", scrape_status="failed")
    with SessionLocal() as s:
        update_book(s, get_book_by_uuid(s, pending), BookUpdate(authors=["刘慈欣"]), auto_lock=False)
        s.commit()
        result = scrape_pending_books(s)
        s.commit()
        assert result["total"] == 1
        assert s.query(Book).filter(Book.uuid == pending).one().scrape_status == "auto_applied"
        assert s.query(Book).filter(Book.uuid == failed).one().scrape_status == "failed"


def test_rescrape_cleans_and_includes_failed(env, monkeypatch):
    _RecordingScraper.queries = []
    _RecordingScraper.answer_for = "三体"
    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [_RecordingScraper])
    # 书名号外带作者，清理时会补上作者，分数才能过自动写入阈值
    dirty_failed = _book(title="刘慈欣《三体》", scrape_status="failed")
    confirmed = _book(title="围城", scrape_status="confirmed")
    with SessionLocal() as s:
        result = scrape_rescrape_books(s)
        s.commit()
        assert result["titles_cleaned"] == 1
        assert result["total"] == 1
        assert result["auto_applied"] == 1
        book = s.query(Book).filter(Book.uuid == dirty_failed).one()
        assert book.title == "三体"
        assert book.scrape_status == "auto_applied"
        assert [ba.author.name for ba in book.authors] == ["刘慈欣"]
        assert s.query(Book).filter(Book.uuid == confirmed).one().title == "围城"
        assert s.query(Book).filter(Book.uuid == confirmed).one().scrape_status == "confirmed"
    assert "三体" in _RecordingScraper.queries
    assert not any("Kindle" in q for q in _RecordingScraper.queries)
