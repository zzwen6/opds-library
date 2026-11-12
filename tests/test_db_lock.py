"""刮削网络请求期间不应占着 SQLite 写锁，确认才不会 500。"""

from __future__ import annotations

import json
import time

from sqlalchemy import text

from app.db import SQLITE_BUSY_TIMEOUT_MS, SessionLocal
from app.models import Book, ScrapeRecord
from app.scrapers.base import Scraper
from app.scrapers.service import apply_candidate, scrape_book
from app.services.books import get_book_by_uuid


def test_busy_timeout_is_at_least_30s(env):
    with SessionLocal() as session:
        value = session.execute(text("PRAGMA busy_timeout")).scalar()
    assert int(value) >= 30_000
    assert SQLITE_BUSY_TIMEOUT_MS >= 30_000


def _other_session_can_write(book_id: int, mark: str) -> None:
    with SessionLocal() as other:
        other.execute(
            text("UPDATE books SET description = :mark WHERE id = :id"),
            {"mark": mark, "id": book_id},
        )
        other.commit()


def test_scrape_does_not_hold_write_lock_during_search(env, monkeypatch):
    with SessionLocal() as session:
        book = Book(title="元灵化身", scrape_status="pending")
        session.add(book)
        session.commit()
        book_id = book.id
        uuid = book.uuid

    wrote: dict[str, object] = {}

    class SlowScraper(Scraper):
        id = "slow"
        display_name = "slow"

        def lookup_isbn(self, isbn: str):
            return []

        def search(self, title, author, language):
            start = time.monotonic()
            try:
                _other_session_can_write(book_id, "concurrent")
                wrote["ok"] = True
                wrote["seconds"] = time.monotonic() - start
            except Exception as exc:  # noqa: BLE001
                wrote["error"] = str(exc)
                wrote["seconds"] = time.monotonic() - start
            return []

    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [SlowScraper])

    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        scrape_book(session, book)
        session.commit()

    assert wrote.get("ok") is True, wrote
    assert wrote["seconds"] < 2, wrote


def test_confirm_downloads_cover_before_holding_write_lock(env, monkeypatch):
    with SessionLocal() as session:
        book = Book(title="元灵化身", scrape_status="needs_review")
        session.add(book)
        session.flush()
        record = ScrapeRecord(
            book_id=book.id,
            source="douban",
            source_id="1",
            confidence=90,
            raw_json=json.dumps(
                {"title": "元灵化身", "tags": ["玄幻"], "cover_url": "https://example.com/c.jpg"},
                ensure_ascii=False,
            ),
            status="candidate",
        )
        session.add(record)
        session.commit()
        book_id = book.id
        uuid = book.uuid
        record_id = record.id

    wrote: dict[str, object] = {}

    def fake_download(url: str) -> dict:
        start = time.monotonic()
        try:
            _other_session_can_write(book_id, "cover-concurrent")
            wrote["ok"] = True
            wrote["seconds"] = time.monotonic() - start
        except Exception as exc:  # noqa: BLE001
            wrote["error"] = str(exc)
            wrote["seconds"] = time.monotonic() - start
        return {"url": url, "content": None, "content_type": None, "error": "skip"}

    monkeypatch.setattr("app.scrapers.service.download_cover_image", fake_download)

    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        apply_candidate(session, book, record_id, mode="manual")
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "元灵化身"
        assert book.scrape_status == "confirmed"

    assert wrote.get("ok") is True, wrote
    assert wrote["seconds"] < 2, wrote
