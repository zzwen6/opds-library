"""重复书检测与合并。

- 文件哈希相同：完全相同的文件，只标记，不删除磁盘文件。
- ISBN 相同：书目或文件内提取到的 ISBN 规范化为 ISBN-13 后相同。
- 书名+作者模糊匹配：低置信度建议，需人工确认。

合并只改数据库：把其它书目的文件/封面/刮削记录挂到主书目，删除其它书目条目，
所有快照写入 EditBatch，可撤销。
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    Book,
    BookFile,
    Cover,
    DuplicateIgnore,
    EditBatch,
    EditChange,
    FieldLock,
    ScrapeRecord,
)
from app.services.batch_edit import undo_field_changes
from app.services.books import book_query_options, get_book_by_uuid, lock_field, locked_fields
from app.services.fields import (
    ALL_FIELDS,
    dumps,
    get_sources,
    get_value,
    is_empty,
    loads,
    set_source,
    set_value,
)
from app.text_norm import author_key, main_title_key, title_key
from app.util import normalize_isbn

MERGE_SOURCE = "merge"


class MergeRequest(BaseModel):
    primary_uuid: str
    other_uuids: list[str] = Field(default_factory=list)


class IgnoreRequest(BaseModel):
    group_key: str
    kind: str = "unknown"
    note: str | None = None


# ---------- 检测 ----------


def _book_brief(book: Book) -> dict:
    return {
        "uuid": book.uuid,
        "title": book.title,
        "authors": get_value(book, "authors"),
        "isbn13": book.isbn13,
        "language": book.language,
        "formats": sorted({f.format for f in book.files if f.missing_at is None}),
        "file_count": sum(1 for f in book.files if f.missing_at is None),
        "locked": sorted(locked_fields(book)),
        "has_cover": bool(book.cover_id or book.covers),
        "added_at": book.added_at,
    }


def _group_key(kind: str, members: list[str]) -> str:
    joined = ",".join(sorted(members))
    if len(joined) > 1500:
        joined = hashlib.sha256(joined.encode()).hexdigest()
    return f"{kind}:{joined}"


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for x in self.parent:
            out.setdefault(self.find(x), []).append(x)
        return [sorted(v) for v in out.values() if len(v) > 1]


def _book_isbns(book: Book) -> set[str]:
    found: set[str] = set()
    for raw in [book.isbn13, book.isbn10] + [f.extracted_isbn for f in book.files]:
        isbn13, _ = normalize_isbn(raw)
        if isbn13:
            found.add(isbn13)
    return found


def _fuzzy_pairs(books: list[Book]) -> dict[tuple[int, int], tuple[float, str]]:
    """返回 {(book_id_a, book_id_b): (置信度, 说明)}。"""
    info = []
    for b in books:
        names = get_value(b, "authors")
        info.append(
            {
                "id": b.id,
                "full": title_key(b.title),
                "main": main_title_key(b.title),
                "authors": {author_key(n) for n in names if author_key(n)},
            }
        )
    pairs: dict[tuple[int, int], tuple[float, str]] = {}

    def add(a: dict, b: dict, conf: float, why: str) -> None:
        key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
        if key not in pairs or pairs[key][0] < conf:
            pairs[key] = (conf, why)

    by_full: dict[str, list[dict]] = {}
    by_main: dict[str, list[dict]] = {}
    for item in info:
        if item["full"]:
            by_full.setdefault(item["full"], []).append(item)
        if item["main"] and len(item["main"]) >= 2:
            by_main.setdefault(item["main"], []).append(item)

    for bucket in by_full.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1 :]:
                if a["authors"] and b["authors"]:
                    if a["authors"] & b["authors"]:
                        add(a, b, 80, "书名与作者规范化后相同")
                else:
                    add(a, b, 60, "书名规范化后相同（缺少作者信息）")
    for bucket in by_main.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1 :]:
                if a["authors"] & b["authors"]:
                    add(a, b, 65, "主书名相同、作者相同（副标题不同）")

    # 同作者下的近似书名（错别字、少量字符差异）
    by_author: dict[str, list[dict]] = {}
    for item in info:
        for key in item["authors"]:
            by_author.setdefault(key, []).append(item)
    for bucket in by_author.values():
        if len(bucket) > 300:
            continue
        for i, a in enumerate(bucket):
            for b in bucket[i + 1 :]:
                if a["full"] == b["full"] or min(len(a["full"]), len(b["full"])) < 4:
                    continue
                ratio = difflib.SequenceMatcher(None, a["full"], b["full"]).ratio()
                if ratio >= 0.9:
                    add(a, b, 55, f"同作者、书名相似度 {ratio:.0%}")
    return pairs


def find_duplicates(session: Session, include_ignored: bool = False) -> dict:
    books = session.scalars(select(Book).options(*book_query_options()).order_by(Book.id)).unique().all()
    by_id = {b.id: b for b in books}
    ignored = {row.group_key for row in session.scalars(select(DuplicateIgnore)).all()}
    groups: list[dict] = []

    # (a) 文件哈希相同
    files_by_hash: dict[str, list[BookFile]] = {}
    for book in books:
        for f in book.files:
            if f.missing_at is None and f.sha256:
                files_by_hash.setdefault(f.sha256, []).append(f)
    for sha, files in files_by_hash.items():
        if len(files) < 2:
            continue
        book_ids = sorted({f.book_id for f in files})
        key = _group_key("hash", [str(f.id) for f in files])
        groups.append(
            {
                "group_key": key,
                "kind": "hash",
                "confidence": 100,
                "reason": "文件内容完全相同（SHA-256 一致），仅标记，不会删除磁盘文件",
                "sha256": sha,
                "files": [
                    {
                        "id": f.id,
                        "rel_path": f.rel_path,
                        "format": f.format,
                        "size_bytes": f.size_bytes,
                        "book_uuid": by_id[f.book_id].uuid,
                        "book_title": by_id[f.book_id].title,
                    }
                    for f in sorted(files, key=lambda x: x.id)
                ],
                "books": [_book_brief(by_id[i]) for i in book_ids],
                "can_merge": len(book_ids) > 1,
                "ignored": key in ignored,
            }
        )

    # (b) ISBN 相同
    uf = _UnionFind()
    isbn_owner: dict[str, int] = {}
    isbn_of_group: dict[int, set[str]] = {}
    for book in books:
        for isbn in _book_isbns(book):
            if isbn in isbn_owner:
                uf.union(isbn_owner[isbn], book.id)
            else:
                isbn_owner[isbn] = book.id
                uf.find(book.id)
    isbn_sets: list[set[int]] = []
    for member_ids in uf.groups():
        isbn_sets.append(set(member_ids))
        shared = set.intersection(*[_book_isbns(by_id[i]) for i in member_ids]) or set.union(
            *[_book_isbns(by_id[i]) for i in member_ids]
        )
        uuids = [by_id[i].uuid for i in member_ids]
        key = _group_key("isbn", uuids)
        groups.append(
            {
                "group_key": key,
                "kind": "isbn",
                "confidence": 95,
                "reason": "ISBN 相同：" + "、".join(sorted(shared)),
                "books": [_book_brief(by_id[i]) for i in member_ids],
                "can_merge": True,
                "ignored": key in ignored,
            }
        )

    # (c) 书名 + 作者模糊匹配
    pairs = _fuzzy_pairs(list(books))
    uf2 = _UnionFind()
    for a, b in pairs:
        uf2.union(a, b)
    for member_ids in uf2.groups():
        members = set(member_ids)
        if any(members <= s for s in isbn_sets):
            continue
        # 已经在同一哈希组里覆盖的两本也不重复提示
        edges = [(v, k) for k, v in pairs.items() if k[0] in members and k[1] in members]
        conf = max(e[0][0] for e in edges)
        reasons = sorted({e[0][1] for e in edges})
        uuids = [by_id[i].uuid for i in member_ids]
        key = _group_key("fuzzy", uuids)
        groups.append(
            {
                "group_key": key,
                "kind": "fuzzy",
                "confidence": conf,
                "reason": "；".join(reasons),
                "books": [_book_brief(by_id[i]) for i in member_ids],
                "can_merge": True,
                "ignored": key in ignored,
            }
        )

    if not include_ignored:
        visible = [g for g in groups if not g["ignored"]]
    else:
        visible = groups
    visible.sort(key=lambda g: (-g["confidence"], g["kind"]))
    for g in visible:
        if g.get("books"):
            g["suggested_primary"] = suggest_primary(g["books"])
    return {
        "groups": visible,
        "total": len(visible),
        "ignored_count": sum(1 for g in groups if g["ignored"]),
    }


def suggest_primary(briefs: list[dict]) -> str:
    """锁定字段最多 > 有 ISBN > 有封面 > 文件最多 > 最早入库。"""
    ranked = sorted(
        briefs,
        key=lambda b: (
            -len(b["locked"]),
            0 if b["isbn13"] else 1,
            0 if b["has_cover"] else 1,
            -b["file_count"],
            b["added_at"],
        ),
    )
    return ranked[0]["uuid"]


def ignore_group(session: Session, req: IgnoreRequest) -> dict:
    row = session.scalar(select(DuplicateIgnore).where(DuplicateIgnore.group_key == req.group_key))
    if row is None:
        row = DuplicateIgnore(group_key=req.group_key, kind=req.kind, note=req.note)
        session.add(row)
        session.flush()
    return {"id": row.id, "group_key": row.group_key}


def unignore_group(session: Session, group_key: str) -> bool:
    row = session.scalar(select(DuplicateIgnore).where(DuplicateIgnore.group_key == group_key))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


# ---------- 合并 ----------

_MERGE_FIELDS = [f for f in ALL_FIELDS]


def _load_merge_books(session: Session, req: MergeRequest) -> tuple[Book, list[Book]]:
    others_uuids = [u for u in dict.fromkeys(req.other_uuids) if u != req.primary_uuid]
    if not others_uuids:
        raise ValueError("请至少选择一本要并入的书")
    primary = get_book_by_uuid(session, req.primary_uuid)
    if primary is None:
        raise LookupError("找不到主书目")
    others = []
    for u in others_uuids:
        b = get_book_by_uuid(session, u)
        if b is None:
            raise LookupError(f"找不到书目 {u}")
        others.append(b)
    return primary, others


def _merge_plan(primary: Book, others: list[Book]) -> list[dict]:
    """每个字段的结果：主书目锁定 → 保留；其它书锁定且有值 → 取其值并锁上；主书目为空 → 取第一个有值的；标签取并集。"""
    p_locked = locked_fields(primary)
    plan: list[dict] = []
    for field in _MERGE_FIELDS:
        current = get_value(primary, field)
        if field in p_locked:
            continue
        chosen = None
        source_uuid = None
        lock = False
        if field == "tags":
            union = sorted(set(current) | {t for o in others for t in get_value(o, field)})
            if union != sorted(current):
                plan.append(
                    {"field": field, "old": current, "new": union, "from": "union", "lock": False}
                )
            continue
        for other in others:
            if field in locked_fields(other) and not is_empty(get_value(other, field)):
                chosen, source_uuid, lock = get_value(other, field), other.uuid, True
                break
        if chosen is None and is_empty(current):
            for other in others:
                value = get_value(other, field)
                if not is_empty(value):
                    chosen, source_uuid = value, other.uuid
                    break
        if chosen is None or chosen == current:
            continue
        plan.append({"field": field, "old": current, "new": chosen, "from": source_uuid, "lock": lock})
    return plan


def preview_merge(session: Session, req: MergeRequest) -> dict:
    primary, others = _load_merge_books(session, req)
    plan = _merge_plan(primary, others)
    files = [
        {"id": f.id, "rel_path": f.rel_path, "format": f.format, "from": b.uuid, "missing": f.missing_at is not None}
        for b in [primary, *others]
        for f in b.files
    ]
    return {
        "primary": _book_brief(primary),
        "others": [_book_brief(o) for o in others],
        "field_changes": plan,
        "kept_locked_fields": sorted(locked_fields(primary)),
        "files": files,
        "formats_after": sorted({f["format"] for f in files if not f["missing"]}),
    }


def _snapshot(book: Book) -> dict:
    fields = {f: get_value(book, f) for f in ALL_FIELDS}
    return {
        "id": book.id,
        "uuid": book.uuid,
        "fields": fields,
        "sort_title": book.sort_title,
        "scrape_status": book.scrape_status,
        "field_sources_json": book.field_sources_json,
        "added_at": book.added_at.isoformat() if book.added_at else None,
        "created_at": book.created_at.isoformat() if book.created_at else None,
        "locks": [{"field_name": l.field_name, "reason": l.reason} for l in book.locks],
        "file_ids": [f.id for f in book.files],
        "cover_ids": [c.id for c in book.covers],
        "scrape_record_ids": [r.id for r in book.scrape_records],
    }


def apply_merge(session: Session, req: MergeRequest) -> dict:
    primary, others = _load_merge_books(session, req)
    plan = _merge_plan(primary, others)
    snapshots = [_snapshot(o) for o in others]
    batch = EditBatch(
        kind="book_merge",
        status="applied",
        summary=f"合并 {len(others)} 本到《{primary.title}》",
        request_json=req.model_dump_json(),
        payload_json=dumps({"primary_uuid": primary.uuid, "primary_id": primary.id, "others": snapshots}),
    )
    session.add(batch)
    session.flush()

    # 1. 文件、封面、刮削记录改挂到主书目（磁盘文件不动）
    other_ids = [o.id for o in others]
    session.execute(update(BookFile).where(BookFile.book_id.in_(other_ids)).values(book_id=primary.id))
    session.execute(
        update(Cover).where(Cover.book_id.in_(other_ids)).values(book_id=primary.id, is_primary=False)
    )
    session.execute(
        update(ScrapeRecord).where(ScrapeRecord.book_id.in_(other_ids)).values(book_id=primary.id)
    )
    # 2. 删除其它书目条目（作者/标签关联与锁随之删除；快照已保存）
    for other in others:
        session.expire(other)
        session.delete(session.get(Book, other.id))
    session.flush()
    session.expire_all()

    # 3. 按计划写主书目字段
    book = get_book_by_uuid(session, primary.uuid)
    for change in plan:
        field = change["field"]
        old_source = get_sources(book).get(field)
        book = set_value(session, book, field, change["new"])
        set_source(book, field, f"{MERGE_SOURCE}:{change['from']}")
        lock_added = False
        if change["lock"] and field not in locked_fields(book):
            lock_field(session, book, field, reason=f"merge:{batch.id}")
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
    book = get_book_by_uuid(session, primary.uuid)
    result = {
        "primary_uuid": book.uuid,
        "merged_uuids": [s["uuid"] for s in snapshots],
        "field_changes": len(plan),
        "formats": sorted({f.format for f in book.files if f.missing_at is None}),
        "file_count": len(book.files),
    }
    batch.result_json = dumps(result)
    session.flush()
    return {"batch_id": batch.id, **result}


def _restore_book(session: Session, snap: dict, primary_id: int, conflicts: list[dict]) -> Book:
    if session.scalar(select(Book).where(Book.uuid == snap["uuid"])) is not None:
        raise ValueError(f"书目 {snap['uuid']} 已存在，无法恢复")
    fields = snap["fields"]
    book = Book(uuid=snap["uuid"], title=fields.get("title") or "未知书名")
    if session.get(Book, snap["id"]) is None:
        book.id = snap["id"]
    isbn13 = fields.get("isbn13")
    if isbn13 and session.scalar(select(Book).where(Book.isbn13 == isbn13)) is not None:
        conflicts.append({"uuid": snap["uuid"], "field": "isbn13", "reason": f"ISBN {isbn13} 已被其它书目占用，未恢复"})
        isbn13 = None
    book.isbn13 = isbn13
    for key in ["subtitle", "isbn10", "language", "description", "published_date", "page_count", "rating", "series_index", "translators"]:
        setattr(book, key, fields.get(key))
    book.sort_title = snap.get("sort_title")
    book.scrape_status = snap.get("scrape_status") or "pending"
    book.field_sources_json = snap.get("field_sources_json")
    if snap.get("added_at"):
        book.added_at = datetime.fromisoformat(snap["added_at"])
    if snap.get("created_at"):
        book.created_at = datetime.fromisoformat(snap["created_at"])
    session.add(book)
    session.flush()
    for field in ["authors", "tags", "publisher", "series"]:
        if not is_empty(fields.get(field)):
            book = set_value(session, book, field, fields[field])
    # 文件、封面、刮削记录挂回（只挂回仍在主书目下的）
    if snap["file_ids"]:
        session.execute(
            update(BookFile)
            .where(BookFile.id.in_(snap["file_ids"]), BookFile.book_id == primary_id)
            .values(book_id=book.id)
        )
    if snap["cover_ids"]:
        session.execute(
            update(Cover)
            .where(Cover.id.in_(snap["cover_ids"]), Cover.book_id == primary_id)
            .values(book_id=book.id)
        )
    if snap["scrape_record_ids"]:
        session.execute(
            update(ScrapeRecord)
            .where(ScrapeRecord.id.in_(snap["scrape_record_ids"]), ScrapeRecord.book_id == primary_id)
            .values(book_id=book.id)
        )
    session.flush()
    session.expire_all()
    book = get_book_by_uuid(session, snap["uuid"])
    cover_id = fields.get("cover")
    if cover_id and any(c.id == cover_id for c in book.covers):
        book = set_value(session, book, "cover", cover_id)
    for lock in snap.get("locks") or []:
        session.add(FieldLock(book_id=book.id, field_name=lock["field_name"], reason=lock.get("reason")))
    session.flush()
    return book


def undo_merge(session: Session, batch: EditBatch) -> dict:
    payload = loads(batch.payload_json) or {}
    primary = get_book_by_uuid(session, payload.get("primary_uuid", ""))
    if primary is None:
        raise ValueError("主书目已不存在，无法撤销合并")
    # 先还原主书目字段（释放 ISBN、封面等），再恢复被并入的书目
    restored, conflicts = undo_field_changes(session, list(batch.changes))
    primary = get_book_by_uuid(session, primary.uuid)
    restored_books = []
    for snap in payload.get("others") or []:
        try:
            book = _restore_book(session, snap, primary.id, conflicts)
            restored_books.append(book.uuid)
        except ValueError as exc:
            conflicts.append({"uuid": snap.get("uuid"), "reason": str(exc)})
    # 主书目若当前封面已被挂回别的书，清掉悬空的 cover_id
    session.expire_all()
    primary = get_book_by_uuid(session, primary.uuid)
    if primary.cover_id and not any(c.id == primary.cover_id for c in primary.covers):
        primary.cover_id = None
    session.flush()
    batch.status = "undone" if not conflicts else "partially_undone"
    batch.undone_at = datetime.now(timezone.utc)
    session.flush()
    return {
        "batch_id": batch.id,
        "restored": restored,
        "restored_books": restored_books,
        "conflicts": conflicts,
        "status": batch.status,
    }
