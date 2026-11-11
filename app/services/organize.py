from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Book, BookFile, OrganizeBatch, OrganizeMove
from app.services.books import book_query_options
from app.util import normalize_space

ILLEGAL_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')
TOKEN_RE = re.compile(r"\{([a-z_]+)\}")
DEFAULT_TEMPLATE = "{author}/{series}/{title}.{ext}"


def library_is_writable(root: Path | None = None) -> bool:
    root = (root or get_settings().library_path).resolve()
    if not root.exists():
        return False
    probe = root / ".opds-library-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def sanitize_component(value: str | None, fallback: str = "未命名") -> str:
    text = unicodedata.normalize("NFC", value or "")
    text = text.replace("/", "／").replace("\\", "＼")
    text = ILLEGAL_CHARS.sub("＿", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(" .")
    return (text or fallback)[:180]


def _tokens_for(book: Book, book_file: BookFile) -> dict[str, str]:
    authors = [ba.author.name for ba in sorted(book.authors, key=lambda x: x.position) if ba.author]
    published = book.published_date or ""
    year = published[:4] if len(published) >= 4 and published[:4].isdigit() else ""
    return {
        "author": sanitize_component(authors[0] if authors else None, "未知作者"),
        "authors": sanitize_component("、".join(authors) if authors else None, "未知作者"),
        "series": sanitize_component(book.series.name if book.series else "", ""),
        "series_index": sanitize_component(book.series_index or "", ""),
        "title": sanitize_component(book.title, book_file.filename or "未命名"),
        "subtitle": sanitize_component(book.subtitle or "", ""),
        "publisher": sanitize_component(book.publisher.name if book.publisher else "", ""),
        "year": sanitize_component(year, ""),
        "isbn": sanitize_component(book.isbn13 or book.isbn10 or "", ""),
        "language": sanitize_component(book.language or "", ""),
        "ext": book_file.format or Path(book_file.filename).suffix.lstrip("."),
        "format": book_file.format or "",
        "filename": Path(book_file.filename).stem,
        "uuid": book.uuid,
    }


def render_rel_path(book: Book, book_file: BookFile, template: str) -> str:
    tokens = _tokens_for(book, book_file)
    unknown = set(TOKEN_RE.findall(template)) - set(tokens)
    if unknown:
        raise ValueError("模板含有未知变量: " + ", ".join(sorted(unknown)))

    def repl(match: re.Match[str]) -> str:
        return tokens.get(match.group(1), "")

    raw = TOKEN_RE.sub(repl, template).replace("\\", "/")
    parts = [p for p in raw.split("/") if p and p not in {".", ".."}]
    if not parts:
        raise ValueError("模板渲染后路径为空")
    # 文件名不能为空
    if parts[-1] in {".epub", ".pdf"} or parts[-1].startswith("."):
        parts[-1] = (tokens["title"] or "未命名") + (parts[-1] if parts[-1].startswith(".") else f".{tokens['ext']}")
    return "/".join(parts)


def _is_self(root: Path, rel: str, src: Path) -> bool:
    dest = (root / rel).resolve()
    try:
        if dest == src.resolve():
            return True
        if src.exists() and dest.exists() and dest.samefile(src):
            return True
    except OSError:
        pass
    return False


def _unique_dest(
    root: Path, rel: str, src: Path, reserved: set[str] | None = None
) -> tuple[str, str | None]:
    """若目标已被其他文件占用（含本批次已预订路径），则追加 (2)、(3)…。"""
    reserved = reserved if reserved is not None else set()

    def available(candidate: str) -> bool:
        dest = (root / candidate).resolve()
        try:
            dest.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"非法目标路径: {candidate}") from exc
        if _is_self(root, candidate, src):
            return True
        if str(Path(candidate)) in reserved:
            return False
        return not dest.exists()

    if available(rel):
        reserved.add(str(Path(rel)))
        return rel, None
    path = Path(rel)
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(2, 1000):
        candidate = str(parent / f"{stem} ({i}){suffix}") if str(parent) != "." else f"{stem} ({i}){suffix}"
        if available(candidate):
            reserved.add(str(Path(candidate)))
            return candidate, f"目标已被占用，改为 {candidate}"
    raise ValueError(f"无法为 {rel} 找到不冲突的文件名")


def preview_organize(session: Session, template: str | None = None) -> dict:
    settings = get_settings()
    root = settings.library_path.resolve()
    template = template or settings.organize_template or DEFAULT_TEMPLATE
    files = session.scalars(
        select(BookFile).where(BookFile.missing_at.is_(None))
    ).all()
    reserved: set[str] = set()
    items = []
    for book_file in files:
        book = session.get(Book, book_file.book_id)
        if book is None:
            continue
        try:
            rel = render_rel_path(book, book_file, template)
            src = (root / book_file.rel_path).resolve()
            final_rel, conflict = _unique_dest(root, rel, src, reserved)
            action = "skip"
            if not src.exists():
                action = "missing"
            elif Path(final_rel) == Path(book_file.rel_path):
                action = "skip"
            else:
                action = "move"
            items.append(
                {
                    "file_id": book_file.id,
                    "book_uuid": book.uuid,
                    "title": book.title,
                    "from_path": book_file.rel_path,
                    "to_path": final_rel,
                    "action": action,
                    "note": conflict,
                }
            )
        except ValueError as exc:
            items.append(
                {
                    "file_id": book_file.id,
                    "book_uuid": book.uuid,
                    "title": book.title,
                    "from_path": book_file.rel_path,
                    "to_path": None,
                    "action": "error",
                    "note": str(exc),
                }
            )
    return {
        "template": template,
        "writable": library_is_writable(root),
        "organize_enabled": settings.organize_enabled,
        "items": items,
        "move_count": sum(1 for i in items if i["action"] == "move"),
    }


def _cleanup_empty_dirs(root: Path, start: Path) -> None:
    try:
        current = start if start.is_dir() else start.parent
        while current != root and (current.parent == root or root in current.parents):
            try:
                next(current.iterdir())
                break
            except StopIteration:
                parent = current.parent
                current.rmdir()
                current = parent
            except OSError:
                break
    except OSError:
        return


def apply_organize(session: Session, template: str | None = None) -> OrganizeBatch:
    settings = get_settings()
    if not settings.organize_enabled:
        raise PermissionError("整理功能未启用（请设置 ORGANIZE_ENABLED=true）")
    root = settings.library_path.resolve()
    if not library_is_writable(root):
        raise PermissionError("书库目录不可写。请将 docker-compose 中 /library 改为可写挂载后再试。")
    preview = preview_organize(session, template)
    batch = OrganizeBatch(status="applied", template=preview["template"])
    session.add(batch)
    session.flush()
    for item in preview["items"]:
        if item["action"] != "move":
            continue
        book_file = session.get(BookFile, item["file_id"])
        if not book_file:
            continue
        src = (root / book_file.rel_path).resolve()
        dest = (root / item["to_path"]).resolve()
        try:
            dest.relative_to(root)
            src.relative_to(root)
        except ValueError as exc:
            raise ValueError("路径越出书库根目录") from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        old_parent = src.parent
        st = dest.stat()
        old_rel = book_file.rel_path
        book_file.rel_path = item["to_path"]
        book_file.filename = dest.name
        book_file.mtime = st.st_mtime
        book_file.inode = getattr(st, "st_ino", None)
        session.add(
            OrganizeMove(
                batch_id=batch.id,
                file_id=book_file.id,
                from_path=old_rel,
                to_path=item["to_path"],
                status="moved",
                note=item.get("note"),
            )
        )
        _cleanup_empty_dirs(root, old_parent)
    session.flush()
    return batch


def rollback_batch(session: Session, batch_id: int) -> OrganizeBatch:
    settings = get_settings()
    if not settings.organize_enabled:
        raise PermissionError("整理功能未启用")
    root = settings.library_path.resolve()
    if not library_is_writable(root):
        raise PermissionError("书库目录不可写，无法回滚")
    batch = session.get(OrganizeBatch, batch_id)
    if not batch:
        raise ValueError("找不到该整理批次")
    if batch.status == "rolled_back":
        raise ValueError("该批次已经回滚")
    moves = list(batch.moves)
    moves.sort(key=lambda m: m.id, reverse=True)
    for move in moves:
        if move.status != "moved":
            continue
        src = (root / move.to_path).resolve()
        dest = (root / move.from_path).resolve()
        try:
            src.relative_to(root)
            dest.relative_to(root)
        except ValueError as exc:
            raise ValueError("回滚路径越出书库根目录") from exc
        if not src.exists():
            move.status = "rollback_missing"
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        final_rel = move.from_path
        if dest.exists() and not (src.exists() and dest.samefile(src) if dest.exists() else False):
            try:
                if dest.exists() and src.exists() and dest.samefile(src):
                    final_rel = move.from_path
                elif dest.exists():
                    final_rel, note = _unique_dest(root, move.from_path, src)
                    dest = (root / final_rel).resolve()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    move.note = (move.note or "") + f"；回滚时冲突，改为 {final_rel}"
            except OSError:
                final_rel, note = _unique_dest(root, move.from_path, src)
                dest = (root / final_rel).resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        book_file = session.get(BookFile, move.file_id) if move.file_id else None
        if book_file:
            st = dest.stat()
            book_file.rel_path = final_rel
            book_file.filename = dest.name
            book_file.mtime = st.st_mtime
            book_file.inode = getattr(st, "st_ino", None)
        move.status = "rolled_back"
        _cleanup_empty_dirs(root, src.parent)
    batch.status = "rolled_back"
    batch.rolled_back_at = datetime.now(timezone.utc)
    session.flush()
    return batch


def batch_to_dict(batch: OrganizeBatch) -> dict:
    return {
        "id": batch.id,
        "status": batch.status,
        "template": batch.template,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "rolled_back_at": batch.rolled_back_at.isoformat() if batch.rolled_back_at else None,
        "moves": [
            {
                "id": m.id,
                "file_id": m.file_id,
                "from_path": m.from_path,
                "to_path": m.to_path,
                "status": m.status,
                "note": m.note,
            }
            for m in batch.moves
        ],
    }
