from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app.config import get_settings
from app.scrapers.base import Candidate, Scraper, register
from app.util import normalize_isbn, normalize_space
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_request_at = 0.0


def reset_rate_limiter() -> None:
    global _last_request_at
    with _lock:
        _last_request_at = 0.0


def _wait_for_interval(seconds: float) -> None:
    global _last_request_at
    if seconds <= 0:
        return
    with _lock:
        now = time.monotonic()
        wait = _last_request_at + seconds - now
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()



# 仅用于 *.doubanio.com 封面下载；不含 cookie、不走代理
DOUBAN_COVER_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def is_douban_cover_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "doubanio.com" or host.endswith(".doubanio.com")


def cover_download_headers(url: str, default_ua: str) -> dict[str, str]:
    """只有豆瓣图片域名且开关打开时，才带常见桌面浏览器头。"""
    settings = get_settings()
    if settings.douban_cover_browser_headers and is_douban_cover_host(url):
        return {
            "User-Agent": DOUBAN_COVER_BROWSER_UA,
            "Accept": "image/*",
            "Referer": "https://book.douban.com/",
        }
    headers = {"User-Agent": default_ua}
    if default_ua:
        headers["User-Agent"] = default_ua
    return headers


def throttle_douban_cover(url: str) -> None:
    """豆瓣封面与元数据请求共用同一低频间隔，避免连续打图片 CDN。"""
    if not is_douban_cover_host(url):
        return
    settings = get_settings()
    _wait_for_interval(settings.douban_min_interval_seconds)

def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = normalize_space(value)
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = normalize_space(str(item) if item is not None else "")
            if text:
                out.append(text)
        return out
    text = normalize_space(str(value))
    return [text] if text else []


def clean_original_title(
    original_title: str | None,
    *,
    title: str | None,
    authors: list[str],
    translators: list[str] | None = None,
) -> str | None:
    """丢掉与书名或作者/译者相同的原作名（已知 douban-book-api 可能把作者填进 original_title）。"""
    text = normalize_space(original_title)
    if not text:
        return None
    title_n = normalize_space(title)
    if title_n and text == title_n:
        return None
    blocked = {normalize_space(name) for name in list(authors) + list(translators or []) if name}
    if text in blocked:
        return None
    return text


def _rating_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw else None
    if isinstance(raw, dict):
        value = raw.get("value")
        try:
            return float(value) if value not in (None, "", 0, 0.0) else None
        except (TypeError, ValueError):
            return None
    return None


def candidate_from_detail(data: dict[str, Any]) -> Candidate | None:
    if not data:
        return None
    title = normalize_space(data.get("title"))
    if not title:
        return None
    authors = _as_list(data.get("author"))
    translators = _as_list(data.get("translator"))
    original = clean_original_title(
        data.get("original_title"),
        title=title,
        authors=authors,
        translators=translators,
    )
    subtitle = normalize_space(data.get("subtitle")) or original
    isbn13, isbn10 = normalize_isbn(data.get("isbn"))
    publishers = _as_list(data.get("publish"))
    series_vals = _as_list(data.get("series"))
    tags = [t for t in _as_list(data.get("labels")) if t]
    douban_id = str(data.get("id") or "") or None
    url = normalize_space(data.get("url"))
    if not url and douban_id:
        url = f"https://book.douban.com/subject/{douban_id}/"
    language = "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in title) else None
    return Candidate(
        source="douban",
        source_id=douban_id,
        source_url=url,
        title=title,
        subtitle=subtitle,
        authors=authors,
        translators=translators,
        isbn13=isbn13,
        isbn10=isbn10,
        publisher=publishers[0] if publishers else None,
        published_date=normalize_space(data.get("publishDate")),
        description=normalize_space(data.get("book_intro")),
        series=series_vals[0] if series_vals else None,
        tags=tags,
        language=language,
        rating=_rating_value(data.get("rating")),
        cover_url=normalize_space(data.get("cover_url")),
        raw=data,
    )


def candidate_from_search_item(item: dict[str, Any]) -> Candidate | None:
    if not item:
        return None
    tpl = item.get("tpl_name") or "search_subject"
    if tpl in {"search_simple", "search_more"}:
        return None
    title = normalize_space(item.get("title"))
    if not title:
        return None
    abstract = item.get("abstract") or ""
    parts = [normalize_space(p) for p in abstract.split("/") if normalize_space(p)]
    authors = [parts[0]] if parts else []
    publisher = parts[1] if len(parts) > 1 else None
    published = parts[2] if len(parts) > 2 else None
    douban_id = str(item.get("id") or "") or None
    url = normalize_space(item.get("url"))
    if not url and douban_id:
        url = f"https://book.douban.com/subject/{douban_id}/"
    return Candidate(
        source="douban",
        source_id=douban_id,
        source_url=url,
        title=title,
        authors=authors,
        publisher=publisher,
        published_date=published,
        rating=_rating_value(item.get("rating")),
        cover_url=normalize_space(item.get("cover_url")),
        language="zh" if any("\u4e00" <= ch <= "\u9fff" for ch in title) else None,
        raw=item,
    )


def _http_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def request_douban(path: str, params: dict | None = None) -> Any:
    """请求本地/侧车 douban-book-api。失败时返回 None，不抛给调用方。"""
    settings = get_settings()
    if not settings.douban_enabled:
        return None
    base = (settings.douban_api_url or "").rstrip("/")
    if not base:
        return None
    url = base + path
    _wait_for_interval(settings.douban_min_interval_seconds)
    try:
        with _http_client(settings.douban_timeout_seconds) as client:
            resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        logger.warning("豆瓣刮削服务不可达 %s: %s", url, exc)
        return None
    if resp.status_code != 200:
        logger.warning("豆瓣刮削服务返回 HTTP %s: %s", resp.status_code, url)
        return None
    try:
        payload = resp.json()
    except ValueError:
        logger.warning("豆瓣刮削服务返回了非 JSON: %s", url)
        return None
    if not isinstance(payload, dict) or not payload.get("success"):
        logger.warning("豆瓣刮削服务业务失败: %s %s", url, payload)
        return None
    return payload.get("data")


@register
class DoubanScraper(Scraper):
    id = "douban"
    display_name = "豆瓣（douban-book-api）"
    requires_api_key = False

    def lookup_isbn(self, isbn: str) -> list[Candidate]:
        isbn13, _ = normalize_isbn(isbn)
        data = request_douban(f"/isbn/{isbn13 or isbn}")
        if not isinstance(data, dict):
            return []
        cand = candidate_from_detail(data)
        return [cand] if cand else []

    def search(self, title: str, author: str | None = None, language: str | None = None) -> list[Candidate]:
        text = normalize_space(title) or ""
        if author:
            # 只把标题交给搜索接口，作者仅用于稍后排序/打分
            text = title
        if not text:
            return []
        data = request_douban("/search", params={"text": text})
        if not isinstance(data, list):
            return []
        light: list[Candidate] = []
        for item in data:
            if isinstance(item, dict):
                cand = candidate_from_search_item(item)
                if cand:
                    light.append(cand)
        if not light:
            return []
        settings = get_settings()
        limit = max(int(settings.douban_search_detail_limit), 0)
        result: list[Candidate] = []
        hydrated = 0
        for cand in light:
            if limit and hydrated < limit and cand.source_id:
                detail = request_douban(f"/subject/{cand.source_id}")
                if isinstance(detail, dict):
                    filled = candidate_from_detail(detail)
                    if filled:
                        result.append(filled)
                        hydrated += 1
                        continue
            result.append(cand)
        return result
