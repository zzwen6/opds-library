"""OPDS 2.0 路由（/opds2），与 /opds（1.2 Atom）并存，共用同一个 HTTP Basic 账号。"""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import Author, Book, BookAuthor, BookTag, Series, Tag
from app.opds import routes as opds1
from app.opds2 import feeds
from app.services.books import book_query_options

_basic = HTTPBasic(auto_error=False)


class OPDS2AuthRequired(Exception):
    """未认证访问 /opds2：返回 401 + OPDS 认证文档。"""


def require_basic_opds2(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    settings = get_settings()
    if credentials is not None:
        user_ok = secrets.compare_digest(credentials.username, settings.auth_username)
        pass_ok = secrets.compare_digest(credentials.password, settings.auth_password)
        if user_ok and pass_ok:
            return credentials.username
    raise OPDS2AuthRequired()


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _json(data: dict, media_type: str = feeds.FEED_TYPE, status_code: int = 200, headers: dict | None = None) -> Response:
    return Response(
        content=json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        media_type=media_type,
        status_code=status_code,
        headers=headers,
    )


def auth_required_response(request: Request) -> Response:
    base = _base(request)
    auth_url = base + f"{feeds.PREFIX}/auth"
    return _json(
        feeds.authentication_document(base),
        media_type=feeds.AUTH_TYPE,
        status_code=401,
        headers={
            # 普通 HTTP 客户端仍会弹出 Basic 登录框
            "WWW-Authenticate": 'Basic realm="OPDS Library"',
            "Link": f'<{auth_url}>; rel="{feeds.AUTH_REL}"; type="{feeds.AUTH_TYPE}"',
        },
    )


# 认证文档本身必须免认证访问
public_router = APIRouter(prefix=feeds.PREFIX)


@public_router.get("/auth")
def opds2_auth_document(request: Request):
    return _json(feeds.authentication_document(_base(request)), media_type=feeds.AUTH_TYPE)


router = APIRouter(prefix=feeds.PREFIX, dependencies=[Depends(require_basic_opds2)])


@router.get("")
@router.get("/")
def opds2_root(request: Request, session: Session = Depends(get_session)):
    return _json(feeds.root_feed(session, _base(request)))


def _pubs(request: Request, session: Session, **kw) -> Response:
    return _json(feeds.publications_feed(session, base=_base(request), **kw))


@router.get("/new")
def opds2_new(
    request: Request,
    page: int = Query(1, ge=1),
    sort: str | None = None,
    lang: str | None = None,
    session: Session = Depends(get_session),
):
    return _pubs(
        request, session, title="最新入库", path=f"{feeds.PREFIX}/new", stmt=select(Book),
        default_sort="added", page=page, sort=sort, lang=lang,
    )


@router.get("/books")
def opds2_books(
    request: Request,
    page: int = Query(1, ge=1),
    sort: str | None = None,
    lang: str | None = None,
    session: Session = Depends(get_session),
):
    return _pubs(
        request, session, title="全部书目", path=f"{feeds.PREFIX}/books", stmt=select(Book),
        default_sort="title", page=page, sort=sort, lang=lang,
    )


@router.get("/search")
def opds2_search(
    request: Request,
    query: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    sort: str | None = None,
    lang: str | None = None,
    session: Session = Depends(get_session),
):
    term = (query if query is not None else q or "").strip()
    stmt = feeds.search_stmt(term) if term else select(Book).where(Book.id.is_(None))
    return _pubs(
        request, session, title=f"搜索：{term}" if term else "搜索",
        path=f"{feeds.PREFIX}/search", stmt=stmt, default_sort="added", page=page,
        sort=sort, lang=lang, extra_params={"query": term} if term else None,
    )


@router.get("/authors")
def opds2_authors(request: Request, session: Session = Depends(get_session)):
    return _json(feeds.authors_feed(session, _base(request)))


@router.get("/authors/{author_id}")
def opds2_author_books(
    author_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    sort: str | None = None,
    lang: str | None = None,
    session: Session = Depends(get_session),
):
    author = session.get(Author, author_id)
    if not author:
        raise HTTPException(status_code=404, detail="找不到该作者")
    stmt = select(Book).where(Book.id.in_(select(BookAuthor.book_id).where(BookAuthor.author_id == author_id)))
    return _pubs(
        request, session, title=author.name, path=f"{feeds.PREFIX}/authors/{author_id}", stmt=stmt,
        default_sort="title", page=page, sort=sort, lang=lang,
    )


@router.get("/series")
def opds2_series(request: Request, session: Session = Depends(get_session)):
    return _json(feeds.series_feed(session, _base(request)))


@router.get("/series/{series_id}")
def opds2_series_books(
    series_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    sort: str | None = None,
    lang: str | None = None,
    session: Session = Depends(get_session),
):
    series = session.get(Series, series_id)
    if not series:
        raise HTTPException(status_code=404, detail="找不到该丛书")
    stmt = select(Book).where(Book.series_id == series_id)
    return _pubs(
        request, session, title=series.name, path=f"{feeds.PREFIX}/series/{series_id}", stmt=stmt,
        custom_order=("丛书顺序", lambda: [Book.series_index, Book.title, Book.id]),
        page=page, sort=sort, lang=lang,
    )


@router.get("/tags")
def opds2_tags(request: Request, session: Session = Depends(get_session)):
    return _json(feeds.tags_feed(session, _base(request)))


@router.get("/tags/{tag_id}")
def opds2_tag_books(
    tag_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    sort: str | None = None,
    lang: str | None = None,
    session: Session = Depends(get_session),
):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="找不到该标签")
    stmt = select(Book).where(Book.id.in_(select(BookTag.book_id).where(BookTag.tag_id == tag_id)))
    return _pubs(
        request, session, title=tag.name, path=f"{feeds.PREFIX}/tags/{tag_id}", stmt=stmt,
        default_sort="title", page=page, sort=sort, lang=lang,
    )


@router.get("/languages")
def opds2_languages(request: Request, session: Session = Depends(get_session)):
    return _json(feeds.languages_feed(session, _base(request)))


@router.get("/languages/{code}")
def opds2_language_books(
    code: str,
    request: Request,
    page: int = Query(1, ge=1),
    sort: str | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Book).where(Book.language == code)
    return _pubs(
        request, session, title=feeds.language_label(code), path=f"{feeds.PREFIX}/languages/{code}",
        stmt=stmt, default_sort="title", page=page, sort=sort,
    )


@router.get("/publications/{book_uuid}")
def opds2_publication(book_uuid: str, request: Request, session: Session = Depends(get_session)):
    book = session.scalars(
        select(Book).where(Book.uuid == book_uuid).options(*book_query_options())
    ).unique().one_or_none()
    if not book or not feeds.available_files(book):
        raise HTTPException(status_code=404, detail="找不到该书或没有可下载的文件")
    return _json(feeds.publication_document(book, _base(request)), media_type=feeds.PUB_TYPE)


# 封面、缩略图、下载与 1.2 共用实现，只是挂在 /opds2 下，保证认证范围一致
router.add_api_route("/covers/{book_uuid}", opds1.opds_cover, methods=["GET"])
router.add_api_route("/thumbs/{book_uuid}", opds1.opds_thumb, methods=["GET"])
router.add_api_route("/download/{file_id}", opds1.opds_download, methods=["GET"])
