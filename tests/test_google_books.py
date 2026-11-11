from __future__ import annotations

import httpx

from app.scrapers.google_books import GoogleBooksScraper, reset_backoff


def test_429_returns_empty_and_backs_off(monkeypatch):
    reset_backoff()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "30"})

    def _client():
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.scrapers.google_books._client", _client)
    scraper = GoogleBooksScraper()
    first = scraper.search("Pride and Prejudice", "Jane Austen")
    assert first == []
    assert calls["n"] == 1
    second = scraper.lookup_isbn("9780141439518")
    assert second == []
    # 退避期内不再发请求
    assert calls["n"] == 1
    reset_backoff()
