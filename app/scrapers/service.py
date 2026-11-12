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
from app.title_clean import clean_title, is_dirty_title, query_titles, scoring_title
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
    # 先向各源发请求，再写库。若先 flush 删除，写锁会一直占到 Open Library 超时（约 20 秒），
    # 期间网页确认会因 database is locked 返回 500。
    authors = _authors_of(book)
    # 书名可能是「Kindle电子书:《三体》」这类文件名式脏标题：查询用清洗后的书名，查不到再逐步缩短
    queries = query_titles(book.title)
    score_title = scoring_title(book.title)
    score_authors = authors
    if not score_authors and book.title:
        hint_author = clean_title(book.title).author
        if hint_author:
            score_authors = [hint_author]
    collected: list[Candidate] = []
    errors: list[str] = []
    for cls in list_scrapers():
        scraper = cls()
        try:
            found: list[Candidate] = []
            if book.isbn13:
                found = scraper.lookup_isbn(book.isbn13)
            for query in queries:
                if found:
                    break
                found = scraper.search(query, authors[0] if authors else None, book.language)
                if not found and book.language:
                    found = scraper.search(query, authors[0] if authors else None, None)
            for cand in found:
                score_candidate(
                    cand,
                    title=score_title,
                    authors=score_authors,
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

    # 网络请求结束后再清旧候选并写入
    for old in list(book.scrape_records):
        if old.status == "candidate":
            session.delete(old)
    session.flush()

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

    # 封面下载放在写库之前，避免确认时长时间占着写锁
    fetched_cover = None
    cover_url = data.get("cover_url")
    if cover_url and "cover" not in locked:
        fetched_cover = download_cover_image(cover_url)

    if payload:
        update_book(session, book, BookUpdate(**payload), auto_lock=False)
        book = session.merge(book)

    if fetched_cover is not None:
        if apply_downloaded_cover(session, book, fetched_cover, record.source):
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
    return _scrape_book_list(session, pending_books_for_batch(session))


def _scrape_book_list(session: Session, books: list[Book], extra: dict | None = None) -> dict:
    from app.services.books import get_book_by_uuid

    result = {"total": len(books), "auto_applied": 0, "needs_review": 0, "failed": 0, "errors": []}
    if extra:
        result.update(extra)
    # 按本提交，避免一本刮削的写操作拖住后面所有书的网络请求
    uuids = [book.uuid for book in books]
    for uuid in uuids:
        book = get_book_by_uuid(session, uuid)
        if book is None:
            result["failed"] += 1
            result["errors"].append(f"{uuid}: 书目已不存在")
            continue
        try:
            scrape_book(session, book)
            session.commit()
            if book.scrape_status == "auto_applied":
                result["auto_applied"] += 1
            elif book.scrape_status == "needs_review":
                result["needs_review"] += 1
            else:
                result["failed"] += 1
        except Exception as exc:
            session.rollback()
            result["failed"] += 1
            result["errors"].append(f"{uuid}: {exc}")
            logger.warning("批量刮削失败 %s: %s", uuid, exc)
    return result


def clean_unlocked_dirty_titles(session: Session) -> list[dict]:
    """把未锁定的脏书名写成清洗结果，并在没有作者时补上从文件名解析出的作者提示。"""
    from app.services.books import BookUpdate, book_query_options, locked_fields, update_book

    books = list(session.scalars(select(Book).options(*book_query_options())).unique().all())
    cleaned: list[dict] = []
    for book in books:
        locked = locked_fields(book)
        if "title" in locked or not is_dirty_title(book.title):
            continue
        hint = clean_title(book.title)
        if not hint.title or not hint.changed:
            continue
        old_title = book.title
        book.title = hint.title
        author_set = None
        if hint.author and not _authors_of(book) and "authors" not in locked:
            update_book(session, book, BookUpdate(authors=[hint.author]), auto_lock=False)
            book = session.merge(book)
            author_set = hint.author
        book.updated_at = datetime.now(timezone.utc)
        cleaned.append({
            "uuid": book.uuid,
            "old_title": old_title,
            "title": book.title,
            "author_set": author_set,
        })
    session.flush()
    return cleaned


def books_for_rescrape(session: Session, cleaned_uuids: set[str] | None = None) -> list[Book]:
    """未刮削、失败、待确认，以及刚清过脏标题且尚未手动确认的书。"""
    from app.services.books import book_query_options

    cleaned_uuids = cleaned_uuids or set()
    books = list(session.scalars(select(Book).options(*book_query_options())).unique().all())
    targets = []
    for book in books:
        if book.scrape_status in {"pending", "failed", "needs_review"}:
            targets.append(book)
        elif book.uuid in cleaned_uuids and book.scrape_status != "confirmed":
            targets.append(book)
    return targets


def scrape_rescrape_books(session: Session) -> dict:
    cleaned = clean_unlocked_dirty_titles(session)
    targets = books_for_rescrape(session, {c["uuid"] for c in cleaned})
    return _scrape_book_list(
        session,
        targets,
        extra={"titles_cleaned": len(cleaned), "cleaned": cleaned},
    )
