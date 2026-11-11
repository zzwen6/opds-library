"""OPDS 2.0（JSON）feed 与出版物清单构建。

规范：
- OPDS 2.0 https://specs.opds.io/opds-2.0 （原 drafts.opds.io/opds-2.0）
- Readium Web Publication Manifest 元数据
- Authentication for OPDS 1.0 https://drafts.opds.io/authentication-for-opds-1.0
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Author, Book, BookAuthor, BookFile, BookTag, Series, Tag
from app.opds.feeds import MIME_BY_FORMAT
from app.services.books import book_query_options

FEED_TYPE = "application/opds+json"
PUB_TYPE = "application/opds-publication+json"
AUTH_TYPE = "application/opds-authentication+json"
AUTH_REL = "http://opds-spec.org/auth/document"
ATOM_NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ_REL = "http://opds-spec.org/acquisition"
BASIC_AUTH_TYPE = "http://opds-spec.org/auth/basic"

PREFIX = "/opds2"

# 多格式时的展示顺序
FORMAT_ORDER = {"epub": 0, "azw3": 1, "mobi": 2, "pdf": 3, "cbz": 4}

LANGUAGE_NAMES = {
    "zh": "中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "fr": "法文",
    "de": "德文",
    "es": "西班牙文",
    "ru": "俄文",
    "it": "意大利文",
}

# 与 webpub-manifest metadata.schema.json 中 BCP 47 校验等价的简化版本
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")

SORTS: dict[str, tuple[str, Callable[[], list]]] = {
    "added": ("最新入库", lambda: [Book.added_at.desc(), Book.id.desc()]),
    "title": ("书名", lambda: [func.lower(Book.sort_title), func.lower(Book.title), Book.id]),
}


# ---------------------------------------------------------------- 工具函数


def _abs(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _iso(dt: datetime | None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _link(href: str, typ: str | None = None, rel: str | list[str] | None = None, title: str | None = None, **extra) -> dict:
    out: dict[str, Any] = {"href": href}
    if rel:
        out["rel"] = rel
    if typ:
        out["type"] = typ
    if title:
        out["title"] = title
    for k, v in extra.items():
        if v is not None:
            out[k] = v
    return out


def normalize_published(value: str | None) -> str | None:
    """把 '2012-8'、'2012'、'2012/08/01' 等统一成 ISO 日期 YYYY-MM-DD。

    只有年份时补 01-01，只有年月时补 01 日；无法识别返回 None。
    """
    if not value:
        return None
    m = re.match(r"^\s*(\d{4})(?:[-/.年](\d{1,2})(?:[-/.月](\d{1,2}))?)?", value)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2) or 1)
    day = int(m.group(3) or 1)
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        try:
            return datetime(year, month if 1 <= month <= 12 else 1, 1).strftime("%Y-%m-%d")
        except ValueError:
            return None


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().replace("_", "-")
    return value if _BCP47.match(value) else None


def series_position(value: str | None) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        num = float(str(value).strip())
    except ValueError:
        return None
    return int(num) if num.is_integer() else num


def available_files(book: Book) -> list[BookFile]:
    """未缺失、内容去重（同 SHA-256 只保留一个）并按格式排序的文件。"""
    seen: set[str] = set()
    files: list[BookFile] = []
    for f in sorted(book.files, key=lambda x: (FORMAT_ORDER.get(x.format, 99), x.id)):
        if f.missing_at is not None:
            continue
        if f.sha256:
            if f.sha256 in seen:
                continue
            seen.add(f.sha256)
        files.append(f)
    return files


def has_available_file():
    """只列出至少有一个未缺失文件的书（OPDS 2 要求每本出版物至少一个获取链接）。"""
    return Book.id.in_(select(BookFile.book_id).where(BookFile.missing_at.is_(None)))


# ---------------------------------------------------------------- 认证文档


def authentication_document(base: str) -> dict:
    return {
        "id": _abs(base, f"{PREFIX}/auth"),
        "title": "我的书库",
        "description": "使用书库的账号和密码登录（HTTP Basic）。",
        "links": [
            _link(_abs(base, PREFIX), FEED_TYPE, rel="start", title="书库首页"),
        ],
        "authentication": [
            {
                "type": BASIC_AUTH_TYPE,
                "labels": {"login": "用户名", "password": "密码"},
            }
        ],
    }


# ---------------------------------------------------------------- 出版物


def publication(book: Book, base: str) -> dict:
    meta: dict[str, Any] = {
        "@type": "http://schema.org/Book",
        "title": book.title or "未知书名",
    }
    if book.subtitle:
        meta["subtitle"] = book.subtitle
    if book.sort_title and book.sort_title != book.title:
        meta["sortAs"] = book.sort_title
    meta["identifier"] = f"urn:isbn:{book.isbn13}" if book.isbn13 else f"urn:uuid:{book.uuid}"
    if book.isbn13:
        meta["altIdentifier"] = [f"urn:uuid:{book.uuid}"]

    authors = []
    for ba in sorted(book.authors, key=lambda x: x.position):
        if not ba.author:
            continue
        a: dict[str, Any] = {"name": ba.author.name}
        if ba.author.sort_name and ba.author.sort_name != ba.author.name:
            a["sortAs"] = ba.author.sort_name
        a["links"] = [_link(_abs(base, f"{PREFIX}/authors/{ba.author.id}"), FEED_TYPE)]
        authors.append(a)
    if authors:
        meta["author"] = authors
    if book.translators:
        names = [n.strip() for n in re.split(r"[、,，;；/]", book.translators) if n.strip()]
        if names:
            meta["translator"] = names
    lang = normalize_language(book.language)
    if lang:
        meta["language"] = lang
    if book.publisher and book.publisher.name:
        meta["publisher"] = book.publisher.name
    published = normalize_published(book.published_date)
    if published:
        meta["published"] = published
    meta["modified"] = _iso(book.updated_at or book.added_at)
    if book.description:
        meta["description"] = book.description
    if book.page_count and book.page_count > 0:
        meta["numberOfPages"] = int(book.page_count)
    subjects = [
        {"name": bt.tag.name, "links": [_link(_abs(base, f"{PREFIX}/tags/{bt.tag.id}"), FEED_TYPE)]}
        for bt in sorted(book.tags, key=lambda x: x.tag.name if x.tag else "")
        if bt.tag
    ]
    if subjects:
        meta["subject"] = subjects
    if book.series:
        series: dict[str, Any] = {
            "name": book.series.name,
            "links": [_link(_abs(base, f"{PREFIX}/series/{book.series.id}"), FEED_TYPE)],
        }
        pos = series_position(book.series_index)
        if pos is not None:
            series["position"] = pos
        meta["belongsTo"] = {"series": [series]}

    links: list[dict] = [
        _link(_abs(base, f"{PREFIX}/publications/{book.uuid}"), PUB_TYPE, rel="self"),
    ]
    for f in available_files(book):
        links.append(
            _link(
                _abs(base, f"{PREFIX}/download/{f.id}"),
                MIME_BY_FORMAT.get(f.format, "application/octet-stream"),
                rel=ACQ_REL,
                title=f"{f.format.upper()} · {f.filename}",
                size=f.size_bytes if f.size_bytes and f.size_bytes > 0 else None,
            )
        )

    out: dict[str, Any] = {"metadata": meta, "links": links}
    cover = book.primary_cover or next((c for c in book.covers if c.is_primary), None)
    if cover:
        img = _link(
            _abs(base, f"{PREFIX}/covers/{book.uuid}"),
            cover.mime or "image/jpeg",
            width=cover.width if cover.width and cover.width > 0 else None,
            height=cover.height if cover.height and cover.height > 0 else None,
        )
        images = [img]
        if cover.thumb_path:
            images.append(_link(_abs(base, f"{PREFIX}/thumbs/{book.uuid}"), "image/jpeg", rel="http://opds-spec.org/image/thumbnail"))
        out["images"] = images
    return out


def publication_document(book: Book, base: str) -> dict:
    doc = publication(book, base)
    doc["links"].append(_link(_abs(base, PREFIX), FEED_TYPE, rel="start", title="书库首页"))
    return doc


# ---------------------------------------------------------------- feed 公共部分


def _common_links(base: str, self_href: str) -> list[dict]:
    return [
        _link(self_href, FEED_TYPE, rel="self"),
        _link(_abs(base, PREFIX), FEED_TYPE, rel="start", title="书库首页"),
        _link(_abs(base, f"{PREFIX}/search{{?query}}"), FEED_TYPE, rel="search", title="搜索", templated=True),
        _link(_abs(base, f"{PREFIX}/auth"), AUTH_TYPE, rel=AUTH_REL),
    ]


def _empty_navigation(base: str) -> list[dict]:
    # schema 要求 navigation/publications 至少一项；结果为空时给一个返回首页的入口
    return [_link(_abs(base, PREFIX), FEED_TYPE, title="暂无条目，返回首页")]


def root_feed(session: Session, base: str) -> dict:
    count = lambda stmt: session.scalar(select(func.count()).select_from(stmt.subquery())) or 0  # noqa: E731
    total = count(select(Book.id).where(has_available_file()))
    nav = [
        ("最新入库", "/new", total),
        ("全部书目", "/books", total),
        ("作者", "/authors", count(select(Author.id))),
        ("丛书", "/series", count(select(Series.id))),
        ("标签", "/tags", count(select(Tag.id))),
        ("语言", "/languages", None),
    ]
    links = _common_links(base, _abs(base, PREFIX))
    links.append(_link(_abs(base, "/opds"), ATOM_NAV_TYPE, rel="alternate", title="OPDS 1.2（Atom）"))
    feed: dict[str, Any] = {
        "metadata": {"title": "我的书库", "modified": _iso(None)},
        "links": links,
        "navigation": [
            _link(
                _abs(base, f"{PREFIX}{href}"),
                FEED_TYPE,
                title=title,
                rel="subsection",
                properties={"numberOfItems": n} if n is not None else None,
            )
            for title, href, n in nav
        ],
    }
    recent = (
        session.scalars(
            select(Book)
            .where(has_available_file())
            .options(*book_query_options())
            .order_by(Book.added_at.desc(), Book.id.desc())
            .limit(10)
        )
        .unique()
        .all()
    )
    if recent:
        feed["groups"] = [
            {
                "metadata": {"title": "最新入库", "numberOfItems": total},
                "links": [_link(_abs(base, f"{PREFIX}/new"), FEED_TYPE, rel="self", title="更多")],
                "publications": [publication(b, base) for b in recent],
            }
        ]
    return feed


def _languages(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        select(Book.language, func.count(Book.id))
        .where(has_available_file(), Book.language.is_not(None), Book.language != "")
        .group_by(Book.language)
        .order_by(func.count(Book.id).desc(), Book.language)
    ).all()
    return [(lang, n) for lang, n in rows]


def language_label(code: str) -> str:
    name = LANGUAGE_NAMES.get(code.split("-")[0].lower())
    return f"{name}（{code}）" if name else code


def publications_feed(
    session: Session,
    *,
    base: str,
    title: str,
    path: str,
    stmt,
    default_sort: str = "added",
    custom_order: tuple[str, Callable[[], list]] | None = None,
    page: int,
    sort: str | None = None,
    lang: str | None = None,
    extra_params: dict[str, str] | None = None,
) -> dict:
    """分页的出版物 feed，带语言/排序 facet。stmt 是只含 where/join 的 select(Book)。"""
    settings = get_settings()
    page_size = max(int(settings.page_size), 1)
    page = max(int(page or 1), 1)
    options: dict[str, tuple[str, Callable[[], list]]] = {}
    if custom_order:
        options["default"] = custom_order
        default_sort = "default"
    options.update(SORTS)
    if default_sort not in options:
        default_sort = "added"
    active = sort if sort in options else default_sort
    stmt = stmt.where(has_available_file())
    facet_base_stmt = stmt
    if lang:
        stmt = stmt.where(Book.language == lang)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = options[active][1]()
    rows = (
        session.scalars(
            stmt.options(*book_query_options()).order_by(*order).offset((page - 1) * page_size).limit(page_size)
        )
        .unique()
        .all()
    )
    last_page = max((total + page_size - 1) // page_size, 1)

    def href(p: int | None = None, *, sort_v: str = active, lang_v: str | None = lang) -> str:
        params: dict[str, str] = dict(extra_params or {})
        if sort_v != default_sort:
            params["sort"] = sort_v
        if lang_v:
            params["lang"] = lang_v
        if p and p > 1:
            params["page"] = str(p)
        q = urlencode(params)
        return _abs(base, path) + (f"?{q}" if q else "")

    links = _common_links(base, href(page))
    links.append(_link(href(1), FEED_TYPE, rel="first"))
    if page > 1:
        links.append(_link(href(min(page - 1, last_page)), FEED_TYPE, rel="previous"))
    if page < last_page:
        links.append(_link(href(page + 1), FEED_TYPE, rel="next"))
    links.append(_link(href(last_page), FEED_TYPE, rel="last"))

    feed: dict[str, Any] = {
        "metadata": {
            "title": title,
            "numberOfItems": total,
            "itemsPerPage": page_size,
            "currentPage": page,
        },
        "links": links,
    }

    # facets：排序 + 语言（都保留另一个 facet 的当前取值，回到第 1 页）
    sort_links = [
        _link(href(sort_v=key), FEED_TYPE, rel="self" if key == active else None, title=label)
        for key, (label, _) in options.items()
    ]
    facets = [{"metadata": {"title": "排序"}, "links": sort_links}]

    lang_rows = session.execute(
        select(Book.language, func.count(Book.id))
        .where(Book.id.in_(facet_base_stmt.with_only_columns(Book.id).order_by(None)))
        .where(Book.language.is_not(None), Book.language != "")
        .group_by(Book.language)
        .order_by(func.count(Book.id).desc(), Book.language)
    ).all()
    if lang_rows or lang:
        all_count = session.scalar(select(func.count()).select_from(facet_base_stmt.subquery())) or 0
        lang_links = [
            _link(
                href(lang_v=None),
                FEED_TYPE,
                rel="self" if not lang else None,
                title="全部语言",
                properties={"numberOfItems": all_count},
            )
        ]
        for code, n in lang_rows:
            lang_links.append(
                _link(
                    href(lang_v=code),
                    FEED_TYPE,
                    rel="self" if lang == code else None,
                    title=language_label(code),
                    properties={"numberOfItems": n},
                )
            )
        facets.append({"metadata": {"title": "语言"}, "links": lang_links})
    feed["facets"] = facets

    if rows:
        feed["publications"] = [publication(b, base) for b in rows]
    else:
        feed["navigation"] = _empty_navigation(base)
    return feed


def navigation_feed(base: str, title: str, path: str, items: list[tuple[str, str, int]]) -> dict:
    nav = [
        _link(_abs(base, href), FEED_TYPE, title=t, properties={"numberOfItems": n}) for t, href, n in items
    ]
    return {
        "metadata": {"title": title, "numberOfItems": len(items)},
        "links": _common_links(base, _abs(base, path)),
        "navigation": nav or _empty_navigation(base),
    }


def authors_feed(session: Session, base: str) -> dict:
    rows = session.execute(
        select(Author, func.count(BookAuthor.book_id))
        .join(BookAuthor)
        .where(BookAuthor.book_id.in_(select(Book.id).where(has_available_file())))
        .group_by(Author.id)
        .order_by(Author.sort_name, Author.name)
    ).all()
    items = [(a.name, f"{PREFIX}/authors/{a.id}", n) for a, n in rows]
    return navigation_feed(base, "作者", f"{PREFIX}/authors", items)


def series_feed(session: Session, base: str) -> dict:
    rows = session.execute(
        select(Series, func.count(Book.id))
        .join(Book, Book.series_id == Series.id)
        .where(has_available_file())
        .group_by(Series.id)
        .order_by(Series.sort_name, Series.name)
    ).all()
    items = [(s.name, f"{PREFIX}/series/{s.id}", n) for s, n in rows]
    return navigation_feed(base, "丛书", f"{PREFIX}/series", items)


def tags_feed(session: Session, base: str) -> dict:
    rows = session.execute(
        select(Tag, func.count(BookTag.book_id))
        .join(BookTag)
        .where(BookTag.book_id.in_(select(Book.id).where(has_available_file())))
        .group_by(Tag.id)
        .order_by(Tag.name)
    ).all()
    items = [(t.name, f"{PREFIX}/tags/{t.id}", n) for t, n in rows]
    return navigation_feed(base, "标签", f"{PREFIX}/tags", items)


def languages_feed(session: Session, base: str) -> dict:
    items = [
        (language_label(code), f"{PREFIX}/languages/{code}", n) for code, n in _languages(session)
    ]
    return navigation_feed(base, "语言", f"{PREFIX}/languages", items)


def search_stmt(q: str):
    like = f"%{q}%"
    author_ids = select(BookAuthor.book_id).join(Author).where(Author.name.ilike(like))
    tag_ids = select(BookTag.book_id).join(Tag).where(Tag.name.ilike(like))
    return select(Book).where(
        Book.title.ilike(like)
        | Book.subtitle.ilike(like)
        | Book.isbn13.ilike(like)
        | Book.id.in_(author_ids)
        | Book.id.in_(tag_ids)
    )


