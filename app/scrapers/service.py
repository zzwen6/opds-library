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
from app.scrapers.priority import sort_candidates
from app.scrapers.douban import (
    cover_download_headers,
    is_douban_cover_host,
    throttle_douban_cover,
)
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
                "title", "subtitle", "authors", "translators", "isbn13", "publisher",
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
        translators=candidate.translators,
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
        "translators": candidate.translators,
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

    collected = sort_candidates(collected, book)
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
    if records and _should_auto_apply(unique[0], book):
        apply_candidate(session, book, records[0].id, mode="auto")
    elif records:
        book.scrape_status = "needs_review"
    else:
        book.scrape_status = "failed"
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


def _cover_http_client(timeout: float, headers: dict[str, str]) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, headers=headers)


def download_cover_image(url: str) -> dict:
    """下载封面。豆瓣图片走限速与可选浏览器头；失败不重试，由调用方保留旧封面。"""
    settings = get_settings()
    headers = cover_download_headers(url, settings.user_agent)
    timeout = settings.douban_timeout_seconds if is_douban_cover_host(url) else 20.0
    if is_douban_cover_host(url):
        throttle_douban_cover(url)
    result = {"url": url, "status_code": None, "content": None, "content_type": None, "error": None, "headers": headers}
    try:
        with _cover_http_client(timeout, headers) as client:
            resp = client.get(url)
            result["status_code"] = resp.status_code
            result["content_type"] = resp.headers.get("content-type", "")
            if resp.status_code in {401, 403, 418, 429}:
                result["error"] = f"HTTP {resp.status_code}"
                logger.warning("下载封面被拒绝 %s：%s，保留现有封面", url, result["error"])
                return result
            resp.raise_for_status()
            ctype = result["content_type"] or ""
            if "html" in ctype.lower() and "image" not in ctype.lower():
                result["error"] = f"非图片响应 ({ctype})"
                logger.warning("下载封面失败 %s：%s，保留现有封面", url, result["error"])
                return result
            if not resp.content:
                result["error"] = f"HTTP {resp.status_code} 空响应"
                logger.warning("下载封面失败 %s：%s，保留现有封面", url, result["error"])
                return result
            result["content"] = resp.content
            return result
    except httpx.TimeoutException as exc:
        result["error"] = f"超时: {exc}"
        logger.warning("下载封面超时 %s：%s，保留现有封面", url, exc)
        return result
    except httpx.HTTPError as exc:
        status = exc.response.status_code if getattr(exc, "response", None) is not None else None
        result["status_code"] = result["status_code"] or status
        result["error"] = f"HTTP {status}: {exc}" if status else str(exc)
        logger.warning("下载封面失败 %s：%s，保留现有封面", url, result["error"])
        return result


def apply_downloaded_cover(session: Session, book: Book, fetched: dict, source: str) -> Cover | None:
    settings = get_settings()
    data = fetched.get("content")
    ctype = fetched.get("content_type") or ""
    if not data:
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


def _apply_cover_from_url(session: Session, book: Book, url: str, source: str) -> Cover | None:
    return apply_downloaded_cover(session, book, download_cover_image(url), source)



FIELD_MAP = {
    "title": "title",
    "subtitle": "subtitle",
    "isbn13": "isbn13",
    "language": "language",
    "description": "description",
    "published_date": "published_date",
    "rating": "rating",
    "authors": "authors",
    "publisher": "publisher",
    "series": "series",
    "tags": "tags",
    "translators": "translators",
}


def current_field_values(book: Book) -> dict:
    return {
        "title": book.title,
        "subtitle": book.subtitle,
        "isbn13": book.isbn13,
        "language": book.language,
        "description": book.description,
        "published_date": book.published_date,
        "rating": book.rating,
        "authors": [ba.author.name for ba in sorted(book.authors, key=lambda x: x.position) if ba.author],
        "publisher": book.publisher.name if book.publisher else None,
        "series": book.series.name if book.series else None,
        "tags": [bt.tag.name for bt in book.tags if bt.tag],
        "translators": book.translators,
        "cover_id": book.cover_id,
        "field_sources": json.loads(book.field_sources_json or "{}"),
    }


def _should_auto_apply(candidate: Candidate, book: Book) -> bool:
    settings = get_settings()
    isbn_exact = bool(book.isbn13 and candidate.isbn13 and book.isbn13 == candidate.isbn13)
    return isbn_exact or (candidate.confidence or 0) >= settings.scrape_auto_threshold


def apply_candidate(session: Session, book: Book, record_id: int, mode: str = "manual") -> Book:
    record = session.scalar(
        select(ScrapeRecord).where(ScrapeRecord.id == record_id, ScrapeRecord.book_id == book.id)
    )
    if record is None:
        raise ValueError("找不到该候选")
    data = json.loads(record.raw_json or "{}")
    locked = locked_fields(book)
    previous = current_field_values(book)
    payload: dict = {}
    applied: list[str] = []
    for src, dest in FIELD_MAP.items():
        if dest in locked:
            continue
        value = data.get(src)
        if value in (None, "", []):
            continue
        if dest == "translators" and isinstance(value, list):
            value = "、".join(str(v) for v in value if v)
            if not value:
                continue
        payload[dest] = value
        applied.append(dest)

    if payload:
        update_book(session, book, BookUpdate(**payload), auto_lock=False)
        book = session.merge(book)

    if data.get("cover_url") and "cover" not in locked:
        if _apply_cover_from_url(session, book, data["cover_url"], record.source):
            applied.append("cover")

    from app.services.books import get_book_by_uuid

    book = get_book_by_uuid(session, book.uuid) or book
    current = current_field_values(book)
    changes = {}
    for field in applied:
        if field == "cover":
            changes["cover"] = {
                "old": previous.get("cover_id"),
                "new": current.get("cover_id"),
                "source": record.source,
            }
        else:
            changes[field] = {
                "old": previous.get(field),
                "new": current.get(field),
                "source": record.source,
            }

    record.fields_applied_json = json.dumps(applied, ensure_ascii=False)
    record.applied_mode = mode
    if mode == "auto":
        record.previous_values_json = json.dumps(
            {"previous": previous, "changes": changes},
            ensure_ascii=False,
        )
        record.status = "auto_applied"
        book.scrape_status = "auto_applied" if applied else "needs_review"
        if not applied:
            record.status = "candidate"
    else:
        record.status = "applied"
        book.scrape_status = "confirmed"

    if applied:
        sources = dict(previous.get("field_sources") or {})
        for field in applied:
            sources[field] = record.source
        book.field_sources_json = json.dumps(sources, ensure_ascii=False)
        for other in book.scrape_records:
            if other.id != record.id and other.status == "candidate":
                other.status = "rejected"

    book.updated_at = datetime.now(timezone.utc)
    session.flush()
    return get_book_by_uuid(session, book.uuid) or book


def confirm_candidate(session: Session, book: Book, record_id: int) -> Book:
    return apply_candidate(session, book, record_id, mode="manual")


def latest_auto_record(book: Book) -> ScrapeRecord | None:
    records = [r for r in book.scrape_records if r.status == "auto_applied" and r.applied_mode == "auto"]
    records.sort(key=lambda r: r.fetched_at or r.id, reverse=True)
    return records[0] if records else None


def undo_auto_apply(session: Session, book: Book) -> Book:
    from app.services.books import get_book_by_uuid
    from app.models import Cover

    record = latest_auto_record(book)
    if record is None or not record.previous_values_json:
        raise ValueError("没有可撤销的自动写入")
    payload = json.loads(record.previous_values_json)
    previous = payload.get("previous") or {}
    changes = payload.get("changes") or {}
    restore: dict = {}
    for field in changes:
        if field == "cover":
            continue
        restore[field] = previous.get(field)
    if restore:
        # 空列表/空字符串也要还原
        update_book(session, book, BookUpdate(**{k: v for k, v in restore.items()}), auto_lock=False)
        book = get_book_by_uuid(session, book.uuid) or book
    if "cover" in changes:
        old_id = previous.get("cover_id")
        book.cover_id = old_id
        for cover in book.covers:
            cover.is_primary = bool(old_id and cover.id == old_id)
    sources = dict(previous.get("field_sources") or {})
    book.field_sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
    record.status = "auto_undone"
    # 仍保留候选供再次确认
    others = [r for r in book.scrape_records if r.id != record.id and r.status in {"rejected", "candidate"}]
    for other in others:
        if other.status == "rejected":
            other.status = "candidate"
    book.scrape_status = "needs_review" if others or True else "pending"
    book.updated_at = datetime.now(timezone.utc)
    session.flush()
    return get_book_by_uuid(session, book.uuid) or book


def pending_books_for_batch(session: Session) -> list[Book]:
    from app.services.books import book_query_options

    return list(
        session.scalars(
            select(Book).options(*book_query_options()).where(Book.scrape_status == "pending")
        ).unique().all()
    )


def scrape_pending_books(session: Session) -> dict:
    books = pending_books_for_batch(session)
    result = {"total": len(books), "auto_applied": 0, "needs_review": 0, "failed": 0, "errors": []}
    for book in books:
        try:
            scrape_book(session, book)
            session.flush()
            if book.scrape_status == "auto_applied":
                result["auto_applied"] += 1
            elif book.scrape_status == "needs_review":
                result["needs_review"] += 1
            else:
                result["failed"] += 1
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append(f"{book.uuid}: {exc}")
            logger.warning("批量刮削失败 %s: %s", book.uuid, exc)
    session.flush()
    return result
