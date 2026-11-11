from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Author, Book, BookAuthor, BookFile, Series, Tag, BookTag
from app.services.books import book_query_options

ATOM = "http://www.w3.org/2005/Atom"
DC = "http://purl.org/dc/terms/"
OPDS_NS = "http://opds-spec.org/2010/catalog"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"
THR = "http://purl.org/syndication/thread/1.0"

NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ENTRY_TYPE = "application/atom+xml;type=entry;profile=opds-catalog"

MIME_BY_FORMAT = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "mobi": "application/x-mobipocket-ebook",
    "azw3": "application/vnd.amazon.ebook",
    "cbz": "application/vnd.comicbook+zip",
}

NSMAP = {None: ATOM, "dcterms": DC, "opds": OPDS_NS, "opensearch": OPENSEARCH, "thr": THR}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _el(parent, name, text=None, ns=ATOM, **attrs):
    el = etree.SubElement(parent, f"{{{ns}}}{name}", {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = str(text)
    return el


def _link(parent, rel: str, href: str, typ: str | None = None, title: str | None = None, **extra):
    attrs = {"rel": rel, "href": href}
    if typ:
        attrs["type"] = typ
    if title:
        attrs["title"] = title
    attrs.update({k: v for k, v in extra.items() if v is not None})
    return etree.SubElement(parent, f"{{{ATOM}}}link", attrs)


def _feed(title: str, ident: str, base: str, updated: str | None = None) -> etree._Element:
    feed = etree.Element(f"{{{ATOM}}}feed", nsmap=NSMAP)
    _el(feed, "id", ident)
    _el(feed, "title", title)
    _el(feed, "updated", updated or _now())
    author = _el(feed, "author")
    _el(author, "name", "OPDS Library")
    return feed


def _abs(base: str, path: str) -> str:
    return base.rstrip("/") + path


def add_common_links(feed, base: str, self_href: str, kind: str = "navigation") -> None:
    typ = NAV_TYPE if kind == "navigation" else ACQ_TYPE
    _link(feed, "self", self_href, typ)
    _link(feed, "start", _abs(base, "/opds"), NAV_TYPE, title="首页")
    _link(feed, "search", _abs(base, "/opds/search.xml"), "application/opensearchdescription+xml", title="搜索")


def root_feed(base: str) -> bytes:
    feed = _feed("我的书库", _abs(base, "/opds"), base)
    add_common_links(feed, base, _abs(base, "/opds"), "navigation")
    entries = [
        ("最新入库", "/opds/new", ACQ_TYPE, "按入库时间查看最近加入的书"),
        ("全部书目", "/opds/books", ACQ_TYPE, "浏览全部图书"),
        ("作者", "/opds/authors", NAV_TYPE, "按作者浏览"),
        ("丛书", "/opds/series", NAV_TYPE, "按丛书浏览"),
        ("标签", "/opds/tags", NAV_TYPE, "按标签浏览"),
    ]
    for title, href, typ, summary in entries:
        entry = _el(feed, "entry")
        _el(entry, "title", title)
        _el(entry, "id", _abs(base, href))
        _el(entry, "updated", _now())
        _el(entry, "content", summary, type="text")
        _link(entry, "subsection", _abs(base, href), typ)
    return etree.tostring(feed, xml_declaration=True, encoding="utf-8", pretty_print=True)


def opensearch_description(base: str) -> bytes:
    ns = "http://a9.com/-/spec/opensearch/1.1/"
    root = etree.Element(f"{{{ns}}}OpenSearchDescription", nsmap={None: ns})
    etree.SubElement(root, f"{{{ns}}}ShortName").text = "OPDS Library"
    etree.SubElement(root, f"{{{ns}}}Description").text = "搜索本地书库"
    etree.SubElement(root, f"{{{ns}}}InputEncoding").text = "UTF-8"
    url = etree.SubElement(
        root,
        f"{{{ns}}}Url",
        type=ACQ_TYPE,
        template=_abs(base, "/opds/search?q={searchTerms}&page={startPage?}"),
    )
    url.set("rel", "results")
    return etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)


def _book_updated(book: Book) -> str:
    dt = book.updated_at or book.added_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_book_entry(feed, book: Book, base: str) -> None:
    entry = _el(feed, "entry")
    _el(entry, "title", book.title)
    _el(entry, "id", f"urn:uuid:{book.uuid}")
    _el(entry, "updated", _book_updated(book))
    for ba in sorted(book.authors, key=lambda x: x.position):
        if ba.author:
            author = _el(entry, "author")
            _el(author, "name", ba.author.name)
            _el(author, "uri", _abs(base, f"/opds/authors/{ba.author.id}"))
    if book.language:
        _el(entry, "language", book.language, ns=DC)
    if book.published_date:
        _el(entry, "issued", book.published_date, ns=DC)
    if book.isbn13:
        _el(entry, "identifier", f"urn:isbn:{book.isbn13}", ns=DC)
    if book.publisher:
        _el(entry, "publisher", book.publisher.name, ns=DC)
    if book.description:
        _el(entry, "content", book.description, type="text")
        _el(entry, "summary", book.description[:400], type="text")
    for bt in book.tags:
        if bt.tag:
            _el(entry, "category", term=bt.tag.name, label=bt.tag.name)
    cover = book.primary_cover or next((c for c in book.covers if c.is_primary), None)
    if cover:
        _link(
            entry,
            "http://opds-spec.org/image",
            _abs(base, f"/opds/covers/{book.uuid}"),
            typ=cover.mime or "image/jpeg",
        )
        _link(
            entry,
            "http://opds-spec.org/image/thumbnail",
            _abs(base, f"/opds/thumbs/{book.uuid}"),
            typ="image/jpeg",
        )
    for f in book.files:
        if f.missing_at is not None:
            continue
        mime = MIME_BY_FORMAT.get(f.format, "application/octet-stream")
        _link(
            entry,
            "http://opds-spec.org/acquisition",
            _abs(base, f"/opds/download/{f.id}"),
            typ=mime,
            title=f.filename,
        )


def paginated_books_feed(
    session: Session,
    *,
    title: str,
    ident: str,
    base: str,
    self_path: str,
    stmt,
    page: int,
    extra_params: dict | None = None,
) -> bytes:
    settings = get_settings()
    page_size = settings.page_size
    page = max(page, 1)
    # stmt 是对 Book 的 select，已带过滤
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.scalar(count_stmt) or 0
    rows = (
        session.scalars(
            stmt.options(*book_query_options())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .unique()
        .all()
    )
    feed = _feed(title, ident, base)
    add_common_links(feed, base, _abs(base, self_path), "acquisition")
    _el(feed, "totalResults", str(total), ns=OPENSEARCH)
    _el(feed, "itemsPerPage", str(page_size), ns=OPENSEARCH)
    _el(feed, "startIndex", str((page - 1) * page_size + 1 if total else 0), ns=OPENSEARCH)

    def page_href(p: int) -> str:
        params = dict(extra_params or {})
        params["page"] = str(p)
        query = urlencode(params)
        path = self_path.split("?")[0]
        return _abs(base, f"{path}?{query}")

    last_page = max((total + page_size - 1) // page_size, 1)
    _link(feed, "first", page_href(1), ACQ_TYPE)
    if page > 1:
        _link(feed, "previous", page_href(page - 1), ACQ_TYPE)
    if page < last_page:
        _link(feed, "next", page_href(page + 1), ACQ_TYPE)
    _link(feed, "last", page_href(last_page), ACQ_TYPE)

    for book in rows:
        append_book_entry(feed, book, base)
    return etree.tostring(feed, xml_declaration=True, encoding="utf-8", pretty_print=True)


def newest_stmt():
    return select(Book).order_by(Book.added_at.desc())


def all_books_stmt():
    return select(Book).order_by(func.lower(Book.sort_title), func.lower(Book.title))


def search_stmt(q: str):
    like = f"%{q}%"
    author_ids = select(BookAuthor.book_id).join(Author).where(Author.name.ilike(like))
    return (
        select(Book)
        .where(
            Book.title.ilike(like)
            | Book.subtitle.ilike(like)
            | Book.isbn13.ilike(like)
            | Book.id.in_(author_ids)
        )
        .order_by(Book.added_at.desc())
    )


def navigation_list_feed(title: str, ident: str, base: str, self_path: str, items: list[dict]) -> bytes:
    feed = _feed(title, ident, base)
    add_common_links(feed, base, _abs(base, self_path), "navigation")
    for item in items:
        entry = _el(feed, "entry")
        _el(entry, "title", item["title"])
        _el(entry, "id", item["id"])
        _el(entry, "updated", _now())
        if item.get("count") is not None:
            _el(entry, "content", f"{item['count']} 本", type="text")
        _link(entry, "subsection", item["href"], ACQ_TYPE)
    return etree.tostring(feed, xml_declaration=True, encoding="utf-8", pretty_print=True)


def authors_feed(session: Session, base: str) -> bytes:
    rows = session.execute(
        select(Author, func.count(BookAuthor.book_id))
        .outerjoin(BookAuthor)
        .group_by(Author.id)
        .order_by(Author.sort_name, Author.name)
    ).all()
    items = [
        {
            "title": author.name,
            "id": _abs(base, f"/opds/authors/{author.id}"),
            "href": _abs(base, f"/opds/authors/{author.id}"),
            "count": count,
        }
        for author, count in rows
    ]
    return navigation_list_feed("作者", _abs(base, "/opds/authors"), base, "/opds/authors", items)


def series_feed(session: Session, base: str) -> bytes:
    rows = session.execute(
        select(Series, func.count(Book.id))
        .outerjoin(Book, Book.series_id == Series.id)
        .group_by(Series.id)
        .order_by(Series.sort_name, Series.name)
    ).all()
    items = [
        {
            "title": series.name,
            "id": _abs(base, f"/opds/series/{series.id}"),
            "href": _abs(base, f"/opds/series/{series.id}"),
            "count": count,
        }
        for series, count in rows
    ]
    return navigation_list_feed("丛书", _abs(base, "/opds/series"), base, "/opds/series", items)


def tags_feed(session: Session, base: str) -> bytes:
    rows = session.execute(
        select(Tag, func.count(BookTag.book_id))
        .outerjoin(BookTag)
        .group_by(Tag.id)
        .order_by(Tag.name)
    ).all()
    items = [
        {
            "title": tag.name,
            "id": _abs(base, f"/opds/tags/{tag.id}"),
            "href": _abs(base, f"/opds/tags/{tag.id}"),
            "count": count,
        }
        for tag, count in rows
    ]
    return navigation_list_feed("标签", _abs(base, "/opds/tags"), base, "/opds/tags", items)
