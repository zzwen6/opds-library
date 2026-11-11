from __future__ import annotations

import httpx
import pytest

from app.config import reset_settings_cache
from app.models import Book
from app.scrapers.base import Candidate
from app.scrapers.douban import (
    DoubanScraper,
    candidate_from_detail,
    clean_original_title,
    request_douban,
    reset_rate_limiter,
)
from app.scrapers.priority import is_chinese_book, sort_candidates


SANTI_DETAIL = {
    "title": "三体",
    "subtitle": "",
    "original_title": "刘慈欣",
    "id": "36892731",
    "isbn": "9787536692930",
    "author": ["刘慈欣"],
    "translator": [],
    "publish": "重庆出版社",
    "publishDate": "2021-1-1",
    "series": "",
    "book_intro": "地球文明向宇宙发出的第一声啼鸣……",
    "labels": [],
    "cover_url": "https://img3.doubanio.com/view/subject/l/public/s34863232.jpg",
    "url": "https://book.douban.com/subject/36892731/",
    "rating": {"count": 47, "value": 9.3},
}

HUOZHE_DETAIL = {
    "title": "活着",
    "subtitle": "",
    "original_title": "",
    "id": "4913064",
    "isbn": "9787506365437",
    "author": ["余华"],
    "translator": [],
    "publish": "作家出版社",
    "publishDate": "2012-8",
    "series": "余华作品（2012版）",
    "book_intro": "讲述了农村人福贵悲惨的人生遭遇。",
    "labels": [],
    "cover_url": "https://img9.doubanio.com/view/subject/l/public/s29869926.jpg",
    "url": "https://book.douban.com/subject/4913064/",
    "rating": {"count": 929556, "value": 9.4},
}


@pytest.fixture
def douban_env(env, monkeypatch):
    monkeypatch.setenv("DOUBAN_ENABLED", "true")
    monkeypatch.setenv("DOUBAN_API_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("DOUBAN_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("DOUBAN_SEARCH_DETAIL_LIMIT", "1")
    reset_settings_cache()
    reset_rate_limiter()
    yield env
    reset_settings_cache()
    reset_rate_limiter()


def test_clean_original_title_drops_author():
    assert clean_original_title("刘慈欣", title="三体", authors=["刘慈欣"]) is None
    assert clean_original_title("三体", title="三体", authors=["刘慈欣"]) is None
    assert clean_original_title("The Three-Body Problem", title="三体", authors=["刘慈欣"]) == "The Three-Body Problem"
    assert clean_original_title("  ", title="三体", authors=["刘慈欣"]) is None


def test_map_detail_drops_bad_original_and_empty_tags():
    cand = candidate_from_detail(SANTI_DETAIL)
    assert cand is not None
    assert cand.source == "douban"
    assert cand.title == "三体"
    assert cand.authors == ["刘慈欣"]
    assert cand.isbn13 == "9787536692930"
    assert cand.publisher == "重庆出版社"
    assert cand.published_date == "2021-1-1"
    assert cand.description.startswith("地球文明")
    assert cand.cover_url.endswith("s34863232.jpg")
    assert cand.rating == 9.3
    assert cand.source_id == "36892731"
    assert "book.douban.com/subject/36892731" in (cand.source_url or "")
    assert cand.subtitle is None  # original_title 与作者相同，已丢弃
    assert cand.tags == []
    assert cand.language == "zh"


def test_map_detail_keeps_real_original_as_subtitle_if_needed():
    data = dict(SANTI_DETAIL)
    data["subtitle"] = ""
    data["original_title"] = "The Three-Body Problem"
    cand = candidate_from_detail(data)
    assert cand.subtitle == "The Three-Body Problem"


def test_map_detail_translators():
    data = {
        "title": "百年孤独",
        "subtitle": "",
        "original_title": "Cien años de soledad",
        "id": "6082808",
        "isbn": "9787544258609",
        "author": ["加西亚·马尔克斯"],
        "translator": ["范晔"],
        "publish": "南海出版公司",
        "publishDate": "2011-6",
        "series": "",
        "book_intro": "简介",
        "cover_url": "https://example.com/cover.jpg",
        "url": "https://book.douban.com/subject/6082808/",
        "rating": {"value": 9.3},
        "labels": [],
    }
    cand = candidate_from_detail(data)
    assert cand.translators == ["范晔"]
    assert cand.subtitle == "Cien años de soledad"


def test_lookup_isbn_uses_http_and_maps(douban_env, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.path.endswith("/isbn/9787506365437")
        return httpx.Response(200, json={"success": True, "data": HUOZHE_DETAIL})

    def _client(timeout: float):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr("app.scrapers.douban._http_client", _client)
    found = DoubanScraper().lookup_isbn("978-7-5063-6543-7")
    assert len(found) == 1
    cand = found[0]
    assert cand.title == "活着"
    assert cand.authors == ["余华"]
    assert cand.isbn13 == "9787506365437"
    assert cand.series == "余华作品(2012版)"  # normalize_space 会把全角括号正规化
    assert cand.rating == 9.4
    assert cand.publisher == "作家出版社"
    assert len(calls) == 1


def test_unreachable_returns_empty(douban_env, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    def _client(timeout: float):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr("app.scrapers.douban._http_client", _client)
    assert DoubanScraper().lookup_isbn("9787506365437") == []
    assert DoubanScraper().search("活着") == []


def test_http_error_and_business_error_degrade(douban_env, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "9781111111111" in str(request.url):
            return httpx.Response(500, json={"success": False, "message": "boom"})
        return httpx.Response(200, json={"success": False, "message": {"name": "TypeError"}})

    def _client(timeout: float):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr("app.scrapers.douban._http_client", _client)
    assert DoubanScraper().lookup_isbn("9781111111111") == []
    assert DoubanScraper().lookup_isbn("9787506365437") == []


def test_disabled_skips_http(douban_env, monkeypatch):
    monkeypatch.setenv("DOUBAN_ENABLED", "false")
    reset_settings_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not request when disabled")

    monkeypatch.setattr("app.scrapers.douban._http_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout))
    assert DoubanScraper().lookup_isbn("9787506365437") == []
    assert request_douban("/isbn/9787506365437") is None


def test_is_chinese_book_isbn_and_title():
    assert is_chinese_book(isbn13="9787506365437", title="Something") is True
    assert is_chinese_book(isbn13="9780141439518", title="Pride and Prejudice") is False
    assert is_chinese_book(isbn13=None, title="活着", authors=["余华"]) is True
    assert is_chinese_book(isbn13="9780141439518", title="Pride", authors=["Jane Austen"]) is False


def test_chinese_books_prefer_douban_on_tie():
    book = Book(title="活着", isbn13="9787506365437")
    ol = Candidate(source="openlibrary", title="活着", isbn13="9787506365437", confidence=100)
    db = Candidate(source="douban", title="活着", isbn13="9787506365437", confidence=100)
    gb = Candidate(source="google_books", title="活着", confidence=80)
    ranked = sort_candidates([ol, gb, db], book)
    assert [c.source for c in ranked] == ["douban", "openlibrary", "google_books"]


def test_non_chinese_books_prefer_openlibrary_on_tie():
    book = Book(title="Pride and Prejudice", isbn13="9780141439518")
    ol = Candidate(source="openlibrary", title="Pride and Prejudice", confidence=90)
    db = Candidate(source="douban", title="Pride and Prejudice", confidence=90)
    ranked = sort_candidates([db, ol], book)
    assert [c.source for c in ranked] == ["openlibrary", "douban"]


def test_higher_score_still_wins_over_priority():
    book = Book(title="Pride and Prejudice", isbn13="9780141439518")
    ol = Candidate(source="openlibrary", confidence=70)
    db = Candidate(source="douban", confidence=95)
    ranked = sort_candidates([ol, db], book)
    assert ranked[0].source == "douban"


def test_candidate_maps_douban_labels_to_tags():
    """douban-book-api（≥ 标签修复版）从页面 criteria 解析出常用标签，映射为候选 tags。"""
    data = dict(HUOZHE_DETAIL)
    data["labels"] = ["余华", "人性", "", "中国文学", "小说"]
    cand = candidate_from_detail(data)
    assert cand.tags == ["余华", "人性", "中国文学", "小说"]
