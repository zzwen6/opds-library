from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Book, Cover, ScrapeRecord
from app.parsers.thumbnails import save_cover_and_thumb
from app.schemas import CandidateOut
from app.scrapers import get_scraper, list_scrapers
from app.scrapers.base import Candidate
from app.scrapers.scoring import score_candidate
from app.services.books import BookUpdate, locked_fields, update_book
from app.config import get_settings
from app.util import normalize_space
import httpx
import logging

logger = logging.getLogger(__name__)


def _authors_of(book: Book) -> list[str]:
    return [ba.author.name for ba in book.authors if ba.author]


def _candidate_to_out(record: ScrapeRecord, candidate: Candidate | None = None) -> CandidateOut:
    if candidate is None:
        data = json.loads(record.raw_json or "{}")
        candidate = Candidate(
            source=record.source,
            source_id=record.source_id,
            source_url=record.source_url,
            **{k: data[k] for k in (
                "title", "subtitle", "authors", "isbn13", "publisher",
                "published_date", "description", "series", "language", "cover_url", "tags",
            ) if k in data},
        )
        candidate.confidence = record.confidence or 0
    return CandidateOut(
        record_id=record.id,
        source=record.source,
        source_id=record.source_id,
        source_url=record.source_url,
        confidence=record.confidence or 0,
        title=candidate.title,
        subtitle=candidate.subtitle,
        authors=candidate.authors,
        isbn13=candidate.isbn13,
        publisher=candidate.publisher,
        published_date=candidate.published_date,
        description=candidate.description,
        series=candidate.series,
        language=candidate.language,
        cover_url=candidate.cover_url,
        tags=candidate.tags,
    )


def _persist(session: Session, book: Book, candidate: Candidate) -> ScrapeRecord:
    payload = {
        "title": candidate.title,
        "subtitle": candidate.subtitle,
        "authors": candidate.authors,
        "isbn13": candidate.isbn13,
        "isbn10": candidate.isbn10,
        "publisher": candidate.publisher,
        "published_date": candidate.published_date,
        "description": candidate.description,
        "series": candidate.series,
        "series_index": candidate.series_index,
        "language": candidate.language,
        "rating": candidate.rating,
        "cover_url": candidate.cover_url,
        "tags": candidate.tags,
    }
    record = ScrapeRecord(
        book_id=book.id,
        source=candidate.source,
        source_id=candidate.source_id,
        source_url=candidate.source_url,
        confidence=candidate.confidence,
        raw_json=json.dumps(payload, ensure_ascii=False),
        status="candidate",
    )
    session.add(record)
    session.flush()
    return record


def scrape_book(session: Session, book: Book) -> list[CandidateOut]:
    # 清除未确认的旧候选
    for old in list(book.scrape_records):
        if old.status == "candidate":
            session.delete(old)
    session.flush()

    authors = _authors_of(book)
    collected: list[Candidate] = []
    errors: list[str] = []
    for cls in list_scrapers():
        scraper = cls()
        try:
            found: list[Candidate] = []
            if book.isbn13:
                found = scraper.lookup_isbn(book.isbn13)
            if not found and book.title:
                found = scraper.search(book.title, authors[0] if authors else None, book.language)
                if not found and book.language:
                    found = scraper.search(book.title, authors[0] if authors else None, None)
            for cand in found:
                score_candidate(
                    cand,
                    title=book.title,
                    authors=authors,
                    isbn13=book.isbn13,
                    language=book.language,
                )
                collected.append(cand)
        except Exception as exc:  # 单个源失败不阻断
            errors.append(f"{scraper.id}: {exc}")
            logger.warning("刮削失败 %s: %s", scraper.id, exc)

    collected.sort(key=lambda c: c.confidence, reverse=True)
    # 去重：同一源同一 source_id 只留一条
    seen: set[tuple[str, str | None]] = set()
    unique: list[Candidate] = []
    for cand in collected:
        key = (cand.source, cand.source_id or cand.isbn13 or cand.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cand)

    records = [_persist(session, book, cand) for cand in unique]
    book.scrape_status = "needs_review" if records else "failed"
    book.updated_at = datetime.now(timezone.utc)
    session.flush()
    return [_candidate_to_out(record, cand) for record, cand in zip(records, unique)]


def list_queue(session: Session) -> list[dict]:
    books = session.scalars(
        select(Book).where(Book.scrape_status == "needs_review").order_by(Book.updated_at.desc())
    ).all()
    items = []
    for book in books:
        recs = [r for r in book.scrape_records if r.status == "candidate"]
        recs.sort(key=lambda r: r.confidence or 0, reverse=True)
        items.append(
            {
                "uuid": book.uuid,
                "title": book.title,
                "authors": _authors_of(book),
                "candidates": [_candidate_to_out(r) for r in recs],
            }
        )
    return items


def _apply_cover_from_url(session: Session, book: Book, url: str, source: str) -> Cover | None:
    settings = get_settings()
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": settings.user_agent}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
            ctype = resp.headers.get("content-type", "")
    except httpx.HTTPError:
        return None
    ext = ".jpg"
    if "png" in ctype:
        ext = ".png"
    rel = f"{book.uuid}{ext}"
    thumb_rel = f"{book.uuid}_thumb.jpg"
    dest = settings.covers_path / rel
    thumb = settings.covers_path / thumb_rel
    width, height, mime = save_cover_and_thumb(data, dest, thumb)
    for existing in book.covers:
        existing.is_primary = False
    cover = Cover(
        book_id=book.id,
        source=source,
        rel_path=rel,
        thumb_path=thumb_rel,
        mime=mime,
        width=width,
        height=height,
        is_primary=True,
    )
    session.add(cover)
    session.flush()
    book.cover_id = cover.id
    return cover


def confirm_candidate(session: Session, book: Book, record_id: int) -> Book:
    record = session.scalar(
        select(ScrapeRecord).where(ScrapeRecord.id == record_id, ScrapeRecord.book_id == book.id)
    )
    if record is None:
        raise ValueError("找不到该候选")
    data = json.loads(record.raw_json or "{}")
    locked = locked_fields(book)
    payload: dict = {}
    mapping = {
        "title": "title",
        "subtitle": "subtitle",
        "isbn13": "isbn13",
        "language": "language",
        "description": "description",
        "published_date": "published_date",
        "authors": "authors",
        "publisher": "publisher",
        "series": "series",
        "tags": "tags",
    }
    applied = []
    for src, dest in mapping.items():
        if dest in locked:
            continue
        value = data.get(src)
        if value in (None, "", []):
            continue
        payload[dest] = value
        applied.append(dest)

    if payload:
        update_book(session, book, BookUpdate(**payload), auto_lock=False)

    if data.get("cover_url") and "cover" not in locked:
        if _apply_cover_from_url(session, book, data["cover_url"], record.source):
            applied.append("cover")

    record.status = "applied"
    record.fields_applied_json = json.dumps(applied, ensure_ascii=False)
    for other in book.scrape_records:
        if other.id != record.id and other.status == "candidate":
            other.status = "rejected"
    book.scrape_status = "confirmed"
    book.updated_at = datetime.now(timezone.utc)
    session.flush()
    from app.services.books import get_book_by_uuid

    return get_book_by_uuid(session, book.uuid) or book
