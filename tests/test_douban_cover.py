from __future__ import annotations

import httpx
import pytest

from app.config import reset_settings_cache
from app.db import SessionLocal
from app.scan import scan_library
from app.scrapers.douban import (
    DOUBAN_COVER_BROWSER_UA,
    cover_download_headers,
    is_douban_cover_host,
    reset_rate_limiter,
)
from app.scrapers.service import _apply_cover_from_url, download_cover_image
from app.services.books import get_book_by_uuid, locked_fields, lock_field
from tests.conftest import make_epub


DOUBAN_IMG = "https://img9.doubanio.com/view/subject/l/public/s29869926.jpg"
OL_IMG = "https://covers.openlibrary.org/b/id/14348537-L.jpg"


@pytest.fixture
def cover_env(env, monkeypatch):
    monkeypatch.setenv("DOUBAN_COVER_BROWSER_HEADERS", "true")
    monkeypatch.setenv("DOUBAN_MIN_INTERVAL_SECONDS", "0")
    reset_settings_cache()
    reset_rate_limiter()
    yield env
    reset_settings_cache()
    reset_rate_limiter()


def _client_factory(handler):
    def _client(timeout, headers):
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
        )

    return _client


def test_is_douban_cover_host_only_doubanio():
    assert is_douban_cover_host(DOUBAN_IMG) is True
    assert is_douban_cover_host("https://img1.doubanio.com/view/subject/s/public/s1.jpg") is True
    assert is_douban_cover_host("https://book.douban.com/subject/4913064/") is False
    assert is_douban_cover_host("https://www.douban.com/favicon.ico") is False
    assert is_douban_cover_host(OL_IMG) is False


def test_browser_headers_only_for_doubanio_when_enabled(cover_env):
    from app.config import get_settings

    default_ua = get_settings().user_agent
    douban = cover_download_headers(DOUBAN_IMG, default_ua)
    other = cover_download_headers(OL_IMG, default_ua)
    assert douban["User-Agent"] == DOUBAN_COVER_BROWSER_UA
    assert douban["Accept"] == "image/*"
    assert douban["Referer"] == "https://book.douban.com/"
    assert other == {"User-Agent": default_ua}
    assert "Referer" not in other
    assert "Accept" not in other


def test_browser_headers_off(cover_env, monkeypatch):
    monkeypatch.setenv("DOUBAN_COVER_BROWSER_HEADERS", "false")
    reset_settings_cache()
    from app.config import get_settings

    default_ua = get_settings().user_agent
    douban = cover_download_headers(DOUBAN_IMG, default_ua)
    assert douban == {"User-Agent": default_ua}
    assert douban["User-Agent"] != DOUBAN_COVER_BROWSER_UA


def test_download_sends_browser_headers_only_to_doubanio(cover_env, monkeypatch):
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), dict(request.headers)))
        return httpx.Response(200, content=b"\xff\xd8\xff\xd9", headers={"content-type": "image/jpeg"})

    monkeypatch.setattr("app.scrapers.service._cover_http_client", _client_factory(handler))
    download_cover_image(DOUBAN_IMG)
    download_cover_image(OL_IMG)
    assert len(seen) == 2
    douban_headers = {k.lower(): v for k, v in seen[0][1].items()}
    ol_headers = {k.lower(): v for k, v in seen[1][1].items()}
    assert douban_headers.get("user-agent") == DOUBAN_COVER_BROWSER_UA
    assert douban_headers.get("accept") == "image/*"
    assert douban_headers.get("referer") == "https://book.douban.com/"
    assert ol_headers.get("user-agent") != DOUBAN_COVER_BROWSER_UA
    assert ol_headers.get("referer") != "https://book.douban.com/"
    assert "image/*" != ol_headers.get("accept") or "referer" not in ol_headers


def test_download_headers_disabled_uses_app_ua(cover_env, monkeypatch):
    monkeypatch.setenv("DOUBAN_COVER_BROWSER_HEADERS", "false")
    reset_settings_cache()
    from app.config import get_settings

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, content=b"\xff\xd8\xff\xd9", headers={"content-type": "image/jpeg"})

    monkeypatch.setattr("app.scrapers.service._cover_http_client", _client_factory(handler))
    result = download_cover_image(DOUBAN_IMG)
    assert result["status_code"] == 200
    headers = {k.lower(): v for k, v in seen[0].items()}
    assert headers.get("user-agent") == get_settings().user_agent
    assert headers.get("referer") != "https://book.douban.com/"


def test_http_418_does_not_retry(cover_env, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(418, content=b"", headers={"content-type": "text/html"})

    monkeypatch.setattr("app.scrapers.service._cover_http_client", _client_factory(handler))
    result = download_cover_image(DOUBAN_IMG)
    assert result["status_code"] == 418
    assert result["content"] is None
    assert result["error"] == "HTTP 418"
    assert calls["n"] == 1


def _book_with_cover(library):
    make_epub(library / "cover-book.epub", "活着", "余华", isbn="9787506365437")
    with SessionLocal() as session:
        scan_library(session, library)
        session.commit()
        from app.models import Book

        book = session.query(Book).one()
        book = get_book_by_uuid(session, book.uuid)
        assert book.cover_id
        return book.uuid, book.cover_id


def test_failure_keeps_old_cover(cover_env, monkeypatch):
    uuid, old_cover_id = _book_with_cover(cover_env["library"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(418, content=b"", headers={"content-type": "text/html"})

    monkeypatch.setattr("app.scrapers.service._cover_http_client", _client_factory(handler))
    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        old_rel = next(c.rel_path for c in book.covers if c.id == old_cover_id)
        old_bytes = (cover_env["settings"].covers_path / old_rel).read_bytes()
        result = _apply_cover_from_url(session, book, DOUBAN_IMG, "douban")
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert result is None
        assert book.cover_id == old_cover_id
        assert next(c for c in book.covers if c.id == old_cover_id).is_primary is True
        assert (cover_env["settings"].covers_path / old_rel).read_bytes() == old_bytes


def test_locked_cover_is_not_downloaded(cover_env, monkeypatch):
    uuid, old_cover_id = _book_with_cover(cover_env["library"])

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("locked cover must not be downloaded")

    monkeypatch.setattr("app.scrapers.service._cover_http_client", _client_factory(handler))
    from app.scrapers.service import apply_candidate
    from app.models import ScrapeRecord
    import json

    with SessionLocal() as session:
        book = get_book_by_uuid(session, uuid)
        lock_field(session, book, "cover")
        session.commit()
        session.expire_all()
        book = get_book_by_uuid(session, uuid)
        assert "cover" in locked_fields(book)
        record = ScrapeRecord(
            book_id=book.id,
            source="douban",
            source_id="4913064",
            confidence=100,
            raw_json=json.dumps({"title": "活着", "cover_url": DOUBAN_IMG}, ensure_ascii=False),
            status="candidate",
        )
        session.add(record)
        session.commit()
        apply_candidate(session, book, record.id, mode="auto")
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.cover_id == old_cover_id
        sources = __import__("json").loads(book.field_sources_json or "{}")
        assert "cover" not in sources
