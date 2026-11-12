from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AuthorOut(BaseModel):
    id: int
    uuid: str
    name: str
    book_count: int = 0

    model_config = {"from_attributes": True}


class SeriesOut(BaseModel):
    id: int
    uuid: str
    name: str
    book_count: int = 0

    model_config = {"from_attributes": True}


class TagOut(BaseModel):
    id: int
    name: str
    kind: str
    book_count: int = 0

    model_config = {"from_attributes": True}


class PublisherOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class FileOut(BaseModel):
    id: int
    rel_path: str
    filename: str
    format: str
    size_bytes: int
    sha256: Optional[str] = None
    missing: bool = False

    model_config = {"from_attributes": True}


class FieldLockOut(BaseModel):
    field_name: str
    locked_at: datetime
    reason: Optional[str] = None

    model_config = {"from_attributes": True}


class CoverOut(BaseModel):
    id: int
    source: str
    mime: str
    width: Optional[int] = None
    height: Optional[int] = None
    is_primary: bool
    url: str
    thumb_url: str


class BookListItem(BaseModel):
    uuid: str
    title: str
    subtitle: Optional[str] = None
    authors: list[str] = Field(default_factory=list)
    language: Optional[str] = None
    isbn13: Optional[str] = None
    formats: list[str] = Field(default_factory=list)
    scrape_status: str
    added_at: datetime
    cover_url: Optional[str] = None
    thumb_url: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    rating: Optional[float] = None
    published_date: Optional[str] = None


class BookDetail(BookListItem):
    description: Optional[str] = None
    isbn10: Optional[str] = None
    publisher: Optional[str] = None
    page_count: Optional[int] = None
    translators: Optional[str] = None
    files: list[FileOut] = Field(default_factory=list)
    locks: list[FieldLockOut] = Field(default_factory=list)
    covers: list[CoverOut] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    can_undo_auto: bool = False


class BookUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    isbn13: Optional[str] = None
    isbn10: Optional[str] = None
    language: Optional[str] = None
    description: Optional[str] = None
    published_date: Optional[str] = None
    page_count: Optional[int] = None
    rating: Optional[float] = None
    series_index: Optional[str] = None
    authors: Optional[list[str]] = None
    publisher: Optional[str] = None
    series: Optional[str] = None
    tags: Optional[list[str]] = None
    translators: Optional[str] = None


class LockRequest(BaseModel):
    field_name: str
    locked: bool = True
    reason: Optional[str] = "manual"


class Page(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


class ScanStatus(BaseModel):
    running: bool
    last_result: Optional[dict] = None
    last_error: Optional[str] = None


class CandidateOut(BaseModel):
    record_id: int
    source: str
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    confidence: float
    title: Optional[str] = None
    subtitle: Optional[str] = None
    authors: list[str] = Field(default_factory=list)
    translators: list[str] = Field(default_factory=list)
    isbn13: Optional[str] = None
    publisher: Optional[str] = None
    published_date: Optional[str] = None
    description: Optional[str] = None
    series: Optional[str] = None
    language: Optional[str] = None
    cover_url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
