"""批量编辑与作者归并：预览 → 执行（字段级记录旧值/来源）→ 撤销。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Author, Book, BookAuthor, EditBatch, EditChange, FieldLock
from app.services.books import book_query_options, get_book_by_uuid, lock_field, locked_fields
from app.services.fields import (
    dumps,
    get_sources,
    get_value,
    loads,
    same_value,
    set_source,
    set_value,
)
from app.text_norm import author_key
from app.util import normalize_space

BATCH_SOURCE = "batch"
MAX_BATCH_BOOKS = 2000


class ListOp(BaseModel):
    mode: Literal["replace", "add", "remove"] = "replace"
    values: list[str] = Field(default_factory=list)


class SeriesOp(BaseModel):
    name: Optional[str] = None  # 空 = 清除系列
    index: Optional[str] = None  # 所有书使用同一序号
    index_start: Optional[float] = None  # 按所选顺序自动编号：start, start+1, ...
    set_index: bool = False  # True 时才改序号（index 为空则清除）


class BatchOps(BaseModel):
    authors: Optional[ListOp] = None
    tags: Optional[ListOp] = None
    series: Optional[SeriesOp] = None
    language: Optional[str] = None
    publisher: Optional[str] = None


class BatchEditRequest(BaseModel):
    book_uuids: list[str] = Field(default_factory=list)
    ops: BatchOps
    lock_after: bool = True  # 与单本手动编辑一致：改过的字段加锁，防止后续刮削覆盖


class AuthorMergeRequest(BaseModel):
    sources: list[str] = Field(default_factory=list)  # 要并掉的作者写法
    target: str  # 统一成的写法
    lock_after: bool = False


def _clean_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        name = normalize_space(raw)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _apply_list_op(current: list[str], op: ListOp) -> list[str]:
    values = _clean_list(op.values)
    if op.mode == "replace":
        return values
    if op.mode == "add":
        return _clean_list(current + values)
    drop = {v.casefold() for v in values}
    return [v for v in current if v.casefold() not in drop]


def _format_index(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _load_books(session: Session, uuids: list[str]) -> list[Book]:
    if not uuids:
        return []
    rows = session.scalars(select(Book).options(*book_query_options()).where(Book.uuid.in_(uuids))).unique().all()
    by_uuid = {b.uuid: b for b in rows}
    return [by_uuid[u] for u in dict.fromkeys(uuids) if u in by_uuid]


def _desired_values(book: Book, ops: BatchOps, position: int) -> dict:
    desired: dict = {}
    if ops.authors is not None:
        desired["authors"] = _apply_list_op(get_value(book, "authors"), ops.authors)
    if ops.tags is not None:
        desired["tags"] = sorted(_apply_list_op(get_value(book, "tags"), ops.tags))
    if ops.series is not None:
        desired["series"] = normalize_space(ops.series.name)
        if ops.series.index_start is not None:
            desired["series_index"] = _format_index(ops.series.index_start + position)
        elif ops.series.set_index:
            desired["series_index"] = normalize_space(ops.series.index)
    if ops.language is not None:
        desired["language"] = normalize_space(ops.language)
    if ops.publisher is not None:
        desired["publisher"] = normalize_space(ops.publisher)
    return desired


def _plan_book(book: Book, desired: dict) -> dict:
    locked = locked_fields(book)
    changes, skipped, unchanged = [], [], []
    for field, new in desired.items():
        old = get_value(book, field)
        if field in locked:
            if not same_value(field, old, new):
                skipped.append({"field": field, "reason": "locked", "current": old, "wanted": new})
            else:
                unchanged.append(field)
            continue
        if same_value(field, old, new):
            unchanged.append(field)
            continue
        changes.append({"field": field, "old": old, "new": new})
    return {
        "uuid": book.uuid,
        "title": book.title,
        "changes": changes,
        "skipped": skipped,
        "unchanged": unchanged,
    }


def _summarize(items: list[dict], missing: list[str]) -> dict:
    return {
        "selected": len(items) + len(missing),
        "affected_books": sum(1 for i in items if i["changes"]),
        "change_count": sum(len(i["changes"]) for i in items),
        "skipped_books": sum(1 for i in items if i["skipped"]),
        "skipped_count": sum(len(i["skipped"]) for i in items),
        "missing": missing,
        "items": items,
    }


def preview_batch_edit(session: Session, req: BatchEditRequest) -> dict:
    if len(req.book_uuids) > MAX_BATCH_BOOKS:
        raise ValueError(f"一次最多 {MAX_BATCH_BOOKS} 本")
    books = _load_books(session, req.book_uuids)
    found = {b.uuid for b in books}
    missing = [u for u in dict.fromkeys(req.book_uuids) if u not in found]
    items = [_plan_book(b, _desired_values(b, req.ops, i)) for i, b in enumerate(books)]
    return _summarize(items, missing)


def _apply_plan(
    session: Session,
    batch: EditBatch,
    items: list[dict],
    lock_after: bool,
    source: str,
) -> None:
    for item in items:
        if not item["changes"]:
            continue
        book = get_book_by_uuid(session, item["uuid"])
        if book is None:
            continue
        for change in item["changes"]:
            field = change["field"]
            old_source = get_sources(book).get(field)
            book = set_value(session, book, field, change["new"])
            set_source(book, field, source)
            lock_added = False
            if lock_after and field not in locked_fields(book):
                lock_field(session, book, field, reason=f"batch:{batch.id}")
                lock_added = True
            session.add(
                EditChange(
                    batch_id=batch.id,
                    book_uuid=book.uuid,
                    field_name=field,
                    old_json=dumps(change["old"]),
                    new_json=dumps(get_value(book, field)),
                    old_source=old_source,
                    lock_added=lock_added,
                )
            )
            book = get_book_by_uuid(session, book.uuid) or book
        book.updated_at = datetime.now(timezone.utc)
    session.flush()


def apply_batch_edit(session: Session, req: BatchEditRequest) -> dict:
    plan = preview_batch_edit(session, req)
    batch = EditBatch(
        kind="batch_edit",
        status="applied",
        summary=f"批量编辑 {plan['affected_books']} 本 / {plan['change_count']} 处",
        request_json=req.model_dump_json(),
    )
    session.add(batch)
    session.flush()
    _apply_plan(session, batch, plan["items"], req.lock_after, BATCH_SOURCE)
    result = {k: v for k, v in plan.items() if k != "items"}
    result["skipped"] = [
        {"uuid": i["uuid"], "title": i["title"], "fields": [s["field"] for s in i["skipped"]]}
        for i in plan["items"]
        if i["skipped"]
    ]
    batch.result_json = dumps(result)
    if not plan["change_count"]:
        batch.status = "noop"
    session.flush()
    return {"batch_id": batch.id, **result}


# ---------- 作者归并 ----------


def author_variants(session: Session) -> list[dict]:
    """把写法不同但规范化后相同的作者分组，作为归并建议。"""
    rows = session.execute(
        select(Author, func.count(BookAuthor.book_id)).outerjoin(BookAuthor).group_by(Author.id)
    ).all()
    groups: dict[str, list[dict]] = {}
    for author, count in rows:
        key = author_key(author.name)
        if not key:
            continue
        groups.setdefault(key, []).append({"id": author.id, "name": author.name, "book_count": count})
    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (-m["book_count"], len(m["name"]), m["name"]))
        out.append({"key": key, "suggested": members[0]["name"], "authors": members})
    out.sort(key=lambda g: -sum(m["book_count"] for m in g["authors"]))
    return out


def _author_merge_plan(session: Session, req: AuthorMergeRequest) -> tuple[list[dict], str]:
    target = normalize_space(req.target)
    if not target:
        raise ValueError("目标作者名不能为空")
    sources = [s for s in _clean_list(req.sources) if s != target]
    if not sources:
        raise ValueError("请至少选择一个要归并的作者写法")
    source_ids = session.scalars(select(Author.id).where(Author.name.in_(sources))).all()
    book_ids = session.scalars(
        select(BookAuthor.book_id).where(BookAuthor.author_id.in_(source_ids)).distinct()
    ).all()
    books = session.scalars(
        select(Book).options(*book_query_options()).where(Book.id.in_(book_ids)).order_by(Book.id)
    ).unique().all()
    source_set = set(sources)
    items = []
    for book in books:
        current = get_value(book, "authors")
        new = _clean_list([target if name in source_set else name for name in current])
        items.append(_plan_book(book, {"authors": new}))
    return items, target


def preview_author_merge(session: Session, req: AuthorMergeRequest) -> dict:
    items, target = _author_merge_plan(session, req)
    return {"target": target, **_summarize(items, [])}


def apply_author_merge(session: Session, req: AuthorMergeRequest) -> dict:
    items, target = _author_merge_plan(session, req)
    plan = _summarize(items, [])
    batch = EditBatch(
        kind="author_merge",
        status="applied",
        summary=f"作者归并 {'、'.join(_clean_list(req.sources))} → {target}（{plan['affected_books']} 本）",
        request_json=req.model_dump_json(),
    )
    session.add(batch)
    session.flush()
    _apply_plan(session, batch, items, req.lock_after, BATCH_SOURCE)
    # 记录别名，清理已无书引用的旧写法
    target_author = session.scalar(select(Author).where(Author.name == target))
    removed: list[str] = []
    for name in _clean_list(req.sources):
        if name == target:
            continue
        author = session.scalar(select(Author).where(Author.name == name))
        if author is None:
            continue
        if target_author is not None:
            aliases = json.loads(target_author.aliases_json or "[]")
            if name not in aliases:
                aliases.append(name)
            target_author.aliases_json = dumps(aliases)
        still_used = session.scalar(select(func.count()).where(BookAuthor.author_id == author.id))
        if not still_used:
            session.delete(author)
            removed.append(name)
    result = {k: v for k, v in plan.items() if k != "items"}
    result["removed_authors"] = removed
    result["skipped"] = [
        {"uuid": i["uuid"], "title": i["title"], "fields": [s["field"] for s in i["skipped"]]}
        for i in items
        if i["skipped"]
    ]
    batch.result_json = dumps(result)
    if not plan["change_count"]:
        batch.status = "noop"
    session.flush()
    return {"batch_id": batch.id, "target": target, **result}


# ---------- 撤销 ----------


def undo_field_changes(session: Session, changes: list[EditChange]) -> tuple[int, list[dict]]:
    """逐字段还原；若字段在之后又被改过（当前值≠本批写入值）则跳过并报告冲突。"""
    restored = 0
    conflicts: list[dict] = []
    for change in reversed(changes):
        if change.status != "applied":
            continue
        book = get_book_by_uuid(session, change.book_uuid)
        if book is None:
            change.status = "conflict"
            conflicts.append({"uuid": change.book_uuid, "field": change.field_name, "reason": "书目已不存在"})
            continue
        field = change.field_name
        new = loads(change.new_json)
        if not same_value(field, get_value(book, field), new):
            change.status = "conflict"
            conflicts.append(
                {"uuid": book.uuid, "title": book.title, "field": field, "reason": "之后又被修改过，未还原"}
            )
            continue
        book = set_value(session, book, field, loads(change.old_json))
        set_source(book, field, change.old_source)
        if change.lock_added:
            lock = session.scalar(
                select(FieldLock).where(FieldLock.book_id == book.id, FieldLock.field_name == field)
            )
            if lock is not None:
                session.delete(lock)
        book.updated_at = datetime.now(timezone.utc)
        change.status = "undone"
        restored += 1
    session.flush()
    return restored, conflicts


def undo_batch(session: Session, batch_id: int) -> dict:
    batch = session.get(EditBatch, batch_id)
    if batch is None:
        raise LookupError("找不到该批次")
    if batch.status not in {"applied"}:
        raise ValueError("该批次不可撤销（已撤销或无改动）")
    if batch.kind == "book_merge":
        from app.services.duplicates import undo_merge

        return undo_merge(session, batch)
    restored, conflicts = undo_field_changes(session, list(batch.changes))
    batch.status = "undone" if not conflicts else "partially_undone"
    batch.undone_at = datetime.now(timezone.utc)
    session.flush()
    return {"batch_id": batch.id, "restored": restored, "conflicts": conflicts, "status": batch.status}


def list_batches(session: Session, limit: int = 50) -> list[dict]:
    rows = session.scalars(select(EditBatch).order_by(EditBatch.id.desc()).limit(limit)).all()
    out = []
    for b in rows:
        out.append(
            {
                "id": b.id,
                "kind": b.kind,
                "status": b.status,
                "summary": b.summary,
                "created_at": b.created_at,
                "undone_at": b.undone_at,
                "change_count": len(b.changes),
                "result": loads(b.result_json),
            }
        )
    return out
