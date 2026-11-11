from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    source: str
    source_id: str | None = None
    source_url: str | None = None
    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = field(default_factory=list)
    translators: list[str] = field(default_factory=list)
    isbn13: str | None = None
    isbn10: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    description: str | None = None
    series: str | None = None
    series_index: str | None = None
    tags: list[str] = field(default_factory=list)
    language: str | None = None
    rating: float | None = None
    cover_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


class Scraper(ABC):
    id: str
    display_name: str
    requires_api_key: bool = False
    capabilities: tuple[str, ...] = ("lookup_isbn", "search", "cover")

    @abstractmethod
    def lookup_isbn(self, isbn: str) -> list[Candidate]:
        ...

    @abstractmethod
    def search(self, title: str, author: str | None = None, language: str | None = None) -> list[Candidate]:
        ...


_REGISTRY: dict[str, type[Scraper]] = {}


def register(cls: type[Scraper]) -> type[Scraper]:
    _REGISTRY[cls.id] = cls
    return cls


def list_scrapers() -> list[type[Scraper]]:
    return list(_REGISTRY.values())


def get_scraper(source_id: str) -> Scraper:
    if source_id not in _REGISTRY:
        raise KeyError(source_id)
    return _REGISTRY[source_id]()
