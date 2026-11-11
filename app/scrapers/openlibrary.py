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


def _from_edition(data: dict[str, Any], source_id: str | None = None) -> Candidate:
    identifiers = data.get("identifiers") or {}
    isbns = data.get("isbn_13") or identifiers.get("isbn_13") or []
    isbn10s = data.get("isbn_10") or identifiers.get("isbn_10") or []
    isbn13, isbn10 = None, None
    if isbns:
        isbn13, isbn10 = normalize_isbn(isbns[0])
    if not isbn13 and isbn10s:
        isbn13, isbn10 = normalize_isbn(isbn10s[0])
    authors = []
    for item in data.get("authors") or []:
        if isinstance(item, dict):
            authors.append(normalize_space(item.get("name") or item.get("key") or "") or "")
        elif isinstance(item, str):
            authors.append(item)
    authors = [a for a in authors if a]
    covers = data.get("covers") or []
    cover_url = None
    if covers:
        cover_url = f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg"
    elif isbn13:
        cover_url = f"https://covers.openlibrary.org/b/isbn/{isbn13}-L.jpg?default=false"
    key = data.get("key") or source_id
    desc = data.get("description")
    if isinstance(desc, dict):
        desc = desc.get("value")
    pubs = data.get("publishers") or []
    return Candidate(
        source="openlibrary",
        source_id=str(key) if key else None,
        source_url=f"https://openlibrary.org{key}" if key and str(key).startswith("/") else None,
        title=normalize_space(data.get("title")),
        subtitle=normalize_space(data.get("subtitle")),
        authors=authors,
        isbn13=isbn13,
        isbn10=isbn10,
        publisher=normalize_space(pubs[0]) if pubs else None,
        published_date=normalize_space(data.get("publish_date") or data.get("publish_year") and str(data.get("publish_year"))),
        description=normalize_space(str(desc)) if desc else None,
        language=(data.get("languages") or [{}])[0].get("key", "").split("/")[-1] or None,
        cover_url=cover_url,
        tags=[s for s in (data.get("subjects") or [])[:12] if isinstance(s, str)],
        raw=data,
    )


@register
class OpenLibraryScraper(Scraper):
    id = "openlibrary"
    display_name = "Open Library"
    requires_api_key = False

    def lookup_isbn(self, isbn: str) -> list[Candidate]:
        isbn13, _ = normalize_isbn(isbn)
        isbn = isbn13 or isbn
        with _client() as client:
            resp = client.get(f"https://openlibrary.org/isbn/{isbn}.json")
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
        cand = _from_edition(data)
        # 补充作者姓名（edition 常常只有 /authors/OL…A）
        if cand.authors and all(a.startswith("/authors/") or a.startswith("OL") for a in cand.authors):
            cand.authors = self._resolve_authors(cand.raw.get("authors") or [])
        return [cand]

    def _resolve_authors(self, entries: list) -> list[str]:
        names: list[str] = []
        with _client() as client:
            for entry in entries[:5]:
                key = entry.get("key") if isinstance(entry, dict) else None
                if not key:
                    if isinstance(entry, dict) and entry.get("name"):
                        names.append(entry["name"])
                    continue
                try:
                    resp = client.get(f"https://openlibrary.org{key}.json")
                    if resp.status_code == 200:
                        name = resp.json().get("name")
                        if name:
                            names.append(name)
                except httpx.HTTPError:
                    continue
        return names

    def search(self, title: str, author: str | None = None, language: str | None = None) -> list[Candidate]:
        params: dict[str, str | int] = {"title": title, "limit": 8}
        if author:
            params["author"] = author
        # Open Library 的 language 过滤器使用 3 位码（eng 而非 en）；2 位码会导致空结果。
        if language and len(language) == 3:
            params["language"] = language
        with _client() as client:
            resp = client.get("https://openlibrary.org/search.json", params=params)
            resp.raise_for_status()
            docs = resp.json().get("docs") or []
        candidates: list[Candidate] = []
        for doc in docs[:8]:
            isbn13 = None
            isbn10 = None
            for raw in doc.get("isbn") or []:
                isbn13, isbn10 = normalize_isbn(raw)
                if isbn13:
                    break
            cover_id = doc.get("cover_i")
            cover_url = (
                f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None
            )
            key = doc.get("key")
            candidates.append(
                Candidate(
                    source="openlibrary",
                    source_id=key,
                    source_url=f"https://openlibrary.org{key}" if key else None,
                    title=normalize_space(doc.get("title")),
                    subtitle=normalize_space(doc.get("subtitle")),
                    authors=list(doc.get("author_name") or []),
                    isbn13=isbn13,
                    isbn10=isbn10,
                    publisher=(doc.get("publisher") or [None])[0],
                    published_date=str(doc.get("first_publish_year") or "") or None,
                    language=(doc.get("language") or [None])[0],
                    cover_url=cover_url,
                    tags=list(doc.get("subject") or [])[:12],
                    raw=doc,
                )
            )
        return candidates
