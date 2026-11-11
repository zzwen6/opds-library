from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import Book, Cover
from app.parsers.thumbnails import save_cover_and_thumb
from app.services.books import book_to_detail, get_book_by_uuid, lock_field

router = APIRouter(tags=["covers"])


def _send(cover: Cover, thumb: bool = False) -> FileResponse:
    settings = get_settings()
    rel = cover.thumb_path if thumb else cover.rel_path
    if not rel:
        raise HTTPException(status_code=404, detail="没有缩略图")
    path = settings.covers_path / rel
    if not path.exists():
        raise HTTPException(status_code=404, detail="封面文件不存在")
    mime = "image/jpeg" if thumb else cover.mime
    return FileResponse(path, media_type=mime)


@router.get("/covers/{cover_id}")
def get_cover(cover_id: int, session: Session = Depends(get_session)) -> FileResponse:
    cover = session.get(Cover, cover_id)
    if not cover:
        raise HTTPException(status_code=404, detail="封面不存在")
    return _send(cover, thumb=False)


@router.get("/covers/{cover_id}/thumb")
def get_thumb(cover_id: int, session: Session = Depends(get_session)) -> FileResponse:
    cover = session.get(Cover, cover_id)
    if not cover:
        raise HTTPException(status_code=404, detail="封面不存在")
    return _send(cover, thumb=True)


@router.post("/books/{book_uuid}/cover")
async def upload_cover(
    book_uuid: str,
    file: UploadFile,
    session: Session = Depends(get_session),
):
    book = get_book_by_uuid(session, book_uuid)
    if not book:
        raise HTTPException(status_code=404, detail="找不到这本书")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    settings = get_settings()
    ext = ".jpg"
    if file.content_type and "png" in file.content_type:
        ext = ".png"
    rel = f"{book.uuid}{ext}"
    thumb_rel = f"{book.uuid}_thumb.jpg"
    try:
        width, height, mime = save_cover_and_thumb(
            data, settings.covers_path / rel, settings.covers_path / thumb_rel
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法解析图片: {exc}") from exc
    for existing in book.covers:
        existing.is_primary = False
    cover = Cover(
        book_id=book.id,
        source="upload",
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
    lock_field(session, book, "cover", reason="upload")
    session.refresh(book)
    return book_to_detail(get_book_by_uuid(session, book.uuid) or book)
