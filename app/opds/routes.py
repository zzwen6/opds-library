from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_basic
from app.config import get_settings
from app.db import get_session
from app.models import Author, Book, BookFile, Series, Tag
from app.opds import feeds
from app.services.books import get_book_by_uuid

router = APIRouter(prefix="/opds", dependencies=[Depends(require_basic)])

XML_TYPE = "application/atom+xml;profile=opds-catalog;charset=utf-8"
NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation;charset=utf-8"
ACQ_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition;charset=utf-8"


def _base(request) -> str:
    return str(request.base_url).rstrip("/")


def _xml(content: bytes, typ: str) -> Response:
    return Response(content=content, media_type=typ)


from fastapi import Request


@router.get("", response_class=Response)
@router.get("/", response_class=Response)
def opds_root(request: Request):
    return _xml(feeds.root_feed(_base(request)), NAV_TYPE)


@router.get("/search.xml", response_class=Response)
def opds_opensearch(request: Request):
    return _xml(feeds.opensearch_description(_base(request)), "application/opensearchdescription+xml")


@router.get("/new", response_class=Response)
def opds_new(request: Request, page: int = 1, session: Session = Depends(get_session)):
    return _xml(
        feeds.paginated_books_feed(
            session,
            title="最新入库",
            ident=_base(request) + "/opds/new",
            base=_base(request),
            self_path="/opds/new",
            stmt=feeds.newest_stmt(),
            page=page,
        ),
        ACQ_TYPE,
    )


@router.get("/books", response_class=Response)
def opds_books(request: Request, page: int = 1, session: Session = Depends(get_session)):
    return _xml(
        feeds.paginated_books_feed(
            session,
            title="全部书目",
            ident=_base(request) + "/opds/books",
            base=_base(request),
            self_path="/opds/books",
            stmt=feeds.all_books_stmt(),
            page=page,
        ),
        ACQ_TYPE,
    )


@router.get("/search", response_class=Response)
def opds_search(
    request: Request,
    q: str = Query("", alias="q"),
    page: int = 1,
    session: Session = Depends(get_session),
):
    return _xml(
        feeds.paginated_books_feed(
            session,
            title=f"搜索：{q}" if q else "搜索",
            ident=_base(request) + "/opds/search",
            base=_base(request),
            self_path="/opds/search",
            stmt=feeds.search_stmt(q or ""),
            page=page,
            extra_params={"q": q} if q else {},
        ),
        ACQ_TYPE,
    )


@router.get("/authors", response_class=Response)
def opds_authors(request: Request, session: Session = Depends(get_session)):
    return _xml(feeds.authors_feed(session, _base(request)), NAV_TYPE)


@router.get("/authors/{author_id}", response_class=Response)
def opds_author_books(
    author_id: int, request: Request, page: int = 1, session: Session = Depends(get_session)
):
    author = session.get(Author, author_id)
    if not author:
        raise HTTPException(status_code=404, detail="找不到该作者")
    from app.models import BookAuthor

    stmt = (
        select(Book)
        .join(BookAuthor, BookAuthor.book_id == Book.id)
        .where(BookAuthor.author_id == author_id)
        .order_by(Book.title)
    )
    return _xml(
        feeds.paginated_books_feed(
            session,
            title=author.name,
            ident=_base(request) + f"/opds/authors/{author_id}",
            base=_base(request),
            self_path=f"/opds/authors/{author_id}",
            stmt=stmt,
            page=page,
        ),
        ACQ_TYPE,
    )


@router.get("/series", response_class=Response)
def opds_series(request: Request, session: Session = Depends(get_session)):
    return _xml(feeds.series_feed(session, _base(request)), NAV_TYPE)


@router.get("/series/{series_id}", response_class=Response)
def opds_series_books(
    series_id: int, request: Request, page: int = 1, session: Session = Depends(get_session)
):
    series = session.get(Series, series_id)
    if not series:
        raise HTTPException(status_code=404, detail="找不到该丛书")
    stmt = select(Book).where(Book.series_id == series_id).order_by(Book.series_index, Book.title)
    return _xml(
        feeds.paginated_books_feed(
            session,
            title=series.name,
            ident=_base(request) + f"/opds/series/{series_id}",
            base=_base(request),
            self_path=f"/opds/series/{series_id}",
            stmt=stmt,
            page=page,
        ),
        ACQ_TYPE,
    )


@router.get("/tags", response_class=Response)
def opds_tags(request: Request, session: Session = Depends(get_session)):
    return _xml(feeds.tags_feed(session, _base(request)), NAV_TYPE)


@router.get("/tags/{tag_id}", response_class=Response)
def opds_tag_books(
    tag_id: int, request: Request, page: int = 1, session: Session = Depends(get_session)
):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="找不到该标签")
    from app.models import BookTag

    stmt = (
        select(Book)
        .join(BookTag, BookTag.book_id == Book.id)
        .where(BookTag.tag_id == tag_id)
        .order_by(Book.title)
    )
    return _xml(
        feeds.paginated_books_feed(
            session,
            title=tag.name,
            ident=_base(request) + f"/opds/tags/{tag_id}",
            base=_base(request),
            self_path=f"/opds/tags/{tag_id}",
            stmt=stmt,
            page=page,
        ),
        ACQ_TYPE,
    )


@router.get("/covers/{book_uuid}")
def opds_cover(book_uuid: str, session: Session = Depends(get_session)):
    book = get_book_by_uuid(session, book_uuid)
    if not book or not book.cover_id:
        raise HTTPException(status_code=404, detail="没有封面")
    from app.models import Cover

    cover = session.get(Cover, book.cover_id)
    if not cover:
        raise HTTPException(status_code=404, detail="没有封面")
    path = get_settings().covers_path / cover.rel_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="封面文件不存在")
    return FileResponse(path, media_type=cover.mime)


@router.get("/thumbs/{book_uuid}")
def opds_thumb(book_uuid: str, session: Session = Depends(get_session)):
    book = get_book_by_uuid(session, book_uuid)
    if not book or not book.cover_id:
        raise HTTPException(status_code=404, detail="没有缩略图")
    from app.models import Cover

    cover = session.get(Cover, book.cover_id)
    if not cover or not cover.thumb_path:
        raise HTTPException(status_code=404, detail="没有缩略图")
    path = get_settings().covers_path / cover.thumb_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="缩略图文件不存在")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/download/{file_id}")
def opds_download(file_id: int, session: Session = Depends(get_session)):
    book_file = session.get(BookFile, file_id)
    if not book_file or book_file.missing_at is not None:
        raise HTTPException(status_code=404, detail="文件不存在")
    root = get_settings().library_path.resolve()
    path = (root / book_file.rel_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法路径") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="磁盘上找不到文件")
    mime = feeds.MIME_BY_FORMAT.get(book_file.format, "application/octet-stream")
    return FileResponse(path, media_type=mime, filename=book_file.filename)
