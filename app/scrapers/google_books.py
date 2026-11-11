from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.scrapers.base import Candidate, Scraper, register
from app.util import normalize_isbn, normalize_space


def _client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        timeout=20.0,
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        follow_redirects=True,
    )


def _from_volume(item: dict[str, Any]) -> Candidate:
    info = item.get("volumeInfo") or {}
    isbn13 = isbn10 = None
    for ident in info.get("industryIdentifiers") or []:
        typ = ident.get("type")
        value = ident.get("identifier")
        if typ == "ISBN_13":
            isbn13, isbn10_c = normalize_isbn(value)
            isbn10 = isbn10 or isbn10_c
        elif typ == "ISBN_10":
            i13, i10 = normalize_isbn(value)
            isbn13 = isbn13 or i13
            isbn10 = isbn10 or i10
    images = info.get("imageLinks") or {}
    cover = images.get("thumbnail") or images.get("smallThumbnail") or images.get("large")
    if cover:
        cover = cover.replace("http://", "https://")
    lang = info.get("language")
    return Candidate(
        source="google_books",
        source_id=item.get("id"),
        source_url=info.get("infoLink") or item.get("selfLink"),
        title=normalize_space(info.get("title")),
        subtitle=normalize_space(info.get("subtitle")),
        authors=list(info.get("authors") or []),
        isbn13=isbn13,
        isbn10=isbn10,
        publisher=normalize_space(info.get("publisher")),
        published_date=normalize_space(info.get("publishedDate")),
        description=normalize_space(info.get("description")),
        language=lang,
        rating=info.get("averageRating"),
        cover_url=cover,
        tags=list(info.get("categories") or []),
        raw=item,
    )


@register
class GoogleBooksScraper(Scraper):
    id = "google_books"
    display_name = "Google Books"
    requires_api_key = False  # 无 key 时仍尝试公开查询；有 key 更稳定

    def _params(self, extra: dict) -> dict:
        settings = get_settings()
        params = dict(extra)
        if settings.google_books_api_key:
            params["key"] = settings.google_books_api_key
        return params

    def lookup_isbn(self, isbn: str) -> list[Candidate]:
        isbn13, _ = normalize_isbn(isbn)
        query = f"isbn:{isbn13 or isbn}"
        return self._search_q(query)

    def search(self, title: str, author: str | None = None, language: str | None = None) -> list[Candidate]:
        parts = [f"intitle:{title}"]
        if author:
            parts.append(f"inauthor:{author}")
        return self._search_q(" ".join(parts), lang=language)

    def _search_q(self, q: str, lang: str | None = None) -> list[Candidate]:
        params = self._params({"q": q, "maxResults": 8, "printType": "books"})
        if lang:
            params["langRestrict"] = lang
        with _client() as client:
            resp = client.get("https://www.googleapis.com/books/v1/volumes", params=params)
            resp.raise_for_status()
            data = resp.json()
        items = data.get("items") or []
        return [_from_volume(item) for item in items]
