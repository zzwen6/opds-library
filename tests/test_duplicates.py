import shutil

from lxml import etree

from app.db import SessionLocal
from app.models import Book, BookFile, EditBatch
from app.scan import scan_library
from app.services.books import get_book_by_uuid, lock_field
from app.text_norm import main_title_key, title_key
from tests.conftest import make_epub, make_pdf


def _scan(env):
    with SessionLocal() as s:
        scan_library(s, env["library"])
        s.commit()


def _uuid(title):
    with SessionLocal() as s:
        return s.query(Book).filter(Book.title == title).one().uuid


def _uuid_by_file(filename):
    with SessionLocal() as s:
        return s.query(BookFile).filter(BookFile.filename == filename).one().book.uuid


def test_title_key_handles_chinese_punctuation():
    assert title_key("《活着》") == title_key("活着")
    assert title_key("活着（精校版）") == title_key("活着")
    assert title_key("三体Ⅱ：黑暗森林") == title_key("三体Ⅱ 黑暗森林")
    assert title_key("ＡＢＣ　Ｂｏｏｋ") == title_key("abc book")
    assert main_title_key("Frankenstein: Or, The Modern Prometheus") == main_title_key("Frankenstein")


def test_exact_hash_duplicate_flagged_never_deleted(client, auth, env):
    lib = env["library"]
    make_epub(lib / "x.epub", "Same", "Author A")
    (lib / "copy").mkdir()
    shutil.copy(lib / "x.epub", lib / "copy" / "x.epub")
    _scan(env)
    data = client.get("/api/duplicates", auth=auth).json()
    hash_groups = [g for g in data["groups"] if g["kind"] == "hash"]
    assert len(hash_groups) == 1
    g = hash_groups[0]
    assert g["confidence"] == 100 and len(g["files"]) == 2
    assert g["can_merge"] is False  # 扫描时已挂到同一本书
    # 标记“不是重复”后不再出现，可取消
    client.post("/api/duplicates/ignore", json={"group_key": g["group_key"], "kind": "hash"}, auth=auth)
    data = client.get("/api/duplicates", auth=auth).json()
    assert not [x for x in data["groups"] if x["kind"] == "hash"] and data["ignored_count"] == 1
    data = client.get("/api/duplicates?include_ignored=true", auth=auth).json()
    assert data["groups"][0]["ignored"] is True
    client.post("/api/duplicates/unignore", json={"group_key": g["group_key"]}, auth=auth)
    assert client.get("/api/duplicates", auth=auth).json()["ignored_count"] == 0
    assert (lib / "x.epub").exists() and (lib / "copy" / "x.epub").exists()


def test_isbn_duplicate_from_file_isbn(client, auth, env):
    from app.util import normalize_isbn

    lib = env["library"]
    make_epub(lib / "a.epub", "Book One", "Author", isbn="9787506365437")
    make_pdf(lib / "b.pdf", "Different Title", "Someone")
    _scan(env)
    _, isbn10 = normalize_isbn("9787506365437")
    with SessionLocal() as s:
        f = s.query(BookFile).filter(BookFile.format == "pdf").one()
        f.extracted_isbn = f"{isbn10[0]}-{isbn10[1:5]}-{isbn10[5:9]}-{isbn10[9]}"  # 同一本书的 ISBN-10 写法
        s.commit()
    data = client.get("/api/duplicates", auth=auth).json()
    isbn_groups = [g for g in data["groups"] if g["kind"] == "isbn"]
    assert len(isbn_groups) == 1 and len(isbn_groups[0]["books"]) == 2
    assert isbn_groups[0]["confidence"] == 95
    assert "9787506365437" in isbn_groups[0]["reason"]


def test_fuzzy_match_and_merge_formats_then_undo(client, auth, env):
    lib = env["library"]
    make_epub(lib / "huozhe.epub", "《活着》", "余华", subject="小说", language="zh")
    make_pdf(lib / "huozhe.pdf", "活着（精校版）", "[中]余华")
    make_epub(lib / "other.epub", "兄弟", "余华")
    _scan(env)
    data = client.get("/api/duplicates", auth=auth).json()
    fuzzy = [g for g in data["groups"] if g["kind"] == "fuzzy"]
    assert len(fuzzy) == 1
    g = fuzzy[0]
    assert len(g["books"]) == 2 and 50 <= g["confidence"] < 95
    epub_uuid = _uuid_by_file("huozhe.epub")
    pdf_uuid = _uuid_by_file("huozhe.pdf")

    # 主书目锁定标题；被并入的书锁定了出版社 → 应带过来并保持锁定
    with SessionLocal() as s:
        p = get_book_by_uuid(s, epub_uuid)
        lock_field(s, p, "title")
        o = get_book_by_uuid(s, pdf_uuid)
        from app.services.books import BookUpdate, update_book

        update_book(s, o, BookUpdate(publisher="作家出版社", title="活着 PDF"), auto_lock=True)
        s.commit()

    req = {"primary_uuid": epub_uuid, "other_uuids": [pdf_uuid]}
    prev = client.post("/api/duplicates/merge/preview", json=req, auth=auth).json()
    assert prev["formats_after"] == ["epub", "pdf"]
    changed = {c["field"]: c for c in prev["field_changes"]}
    assert "title" not in changed  # 主书目锁定字段保留
    assert changed["publisher"]["new"] == "作家出版社" and changed["publisher"]["lock"] is True

    res = client.post("/api/duplicates/merge", json=req, auth=auth).json()
    assert res["formats"] == ["epub", "pdf"]
    assert client.get(f"/api/books/{pdf_uuid}", auth=auth).status_code == 404
    d = client.get(f"/api/books/{epub_uuid}", auth=auth).json()
    # 扫描时「《活着》」这类脏内嵌标题会被清洗成「活着」
    assert d["title"] == "活着" and d["publisher"] == "作家出版社"
    assert {f["format"] for f in d["files"]} == {"epub", "pdf"}
    assert "publisher" in {l["field_name"] for l in d["locks"]}
    assert (lib / "huozhe.pdf").exists() and (lib / "huozhe.epub").exists()

    # OPDS 条目暴露两个获取链接
    feed = client.get("/opds/books", auth=auth)
    assert feed.status_code == 200
    root = etree.fromstring(feed.content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = [e for e in root.findall("a:entry", ns) if e.findtext("a:id", namespaces=ns) == f"urn:uuid:{epub_uuid}"]
    assert entry
    acq = [l for l in entry[0].findall("a:link", ns) if l.get("rel") == "http://opds-spec.org/acquisition"]
    assert len(acq) == 2

    # 合并后不再提示
    assert not [x for x in client.get("/api/duplicates", auth=auth).json()["groups"] if x["kind"] == "fuzzy"]

    # 撤销：恢复被并入的书（同 uuid）、文件挂回、主书目字段与锁还原
    undo = client.post(f"/api/edit-batches/{res['batch_id']}/undo", auth=auth).json()
    assert undo["status"] == "undone", undo
    assert undo["restored_books"] == [pdf_uuid]
    o = client.get(f"/api/books/{pdf_uuid}", auth=auth).json()
    assert o["title"] == "活着 PDF" and o["publisher"] == "作家出版社"
    assert [f["format"] for f in o["files"]] == ["pdf"]
    assert {"title", "publisher"} <= {l["field_name"] for l in o["locks"]}
    p = client.get(f"/api/books/{epub_uuid}", auth=auth).json()
    assert p["publisher"] is None and [f["format"] for f in p["files"]] == ["epub"]
    assert "publisher" not in {l["field_name"] for l in p["locks"]}
    with SessionLocal() as s:
        assert s.query(EditBatch).one().status == "undone"


def test_merge_moves_isbn_from_other(client, auth, env):
    lib = env["library"]
    make_epub(lib / "a.epub", "Alpha", "X")
    make_epub(lib / "b.epub", "Alpha", "X", isbn="9780141439518")
    _scan(env)
    with SessionLocal() as s:
        ub = s.query(Book).filter(Book.isbn13.is_not(None)).one().uuid
        ua = s.query(Book).filter(Book.isbn13.is_(None)).one().uuid
    res = client.post("/api/duplicates/merge", json={"primary_uuid": ua, "other_uuids": [ub]}, auth=auth)
    assert res.status_code == 200, res.text
    assert client.get(f"/api/books/{ua}", auth=auth).json()["isbn13"] == "9780141439518"
    undo = client.post(f"/api/edit-batches/{res.json()['batch_id']}/undo", auth=auth).json()
    assert undo["status"] == "undone", undo
    assert client.get(f"/api/books/{ub}", auth=auth).json()["isbn13"] == "9780141439518"
    assert client.get(f"/api/books/{ua}", auth=auth).json()["isbn13"] is None


def test_merge_validation(client, auth, env):
    lib = env["library"]
    make_epub(lib / "a.epub", "Alpha", "X")
    _scan(env)
    ua = _uuid("Alpha")
    r = client.post("/api/duplicates/merge", json={"primary_uuid": ua, "other_uuids": [ua]}, auth=auth)
    assert r.status_code == 400
    r = client.post("/api/duplicates/merge", json={"primary_uuid": ua, "other_uuids": ["nope"]}, auth=auth)
    assert r.status_code == 404


def test_opds_hides_identical_copies(client, auth, env):
    lib = env["library"]
    make_epub(lib / "x.epub", "Copyable", "Author A")
    (lib / "dup").mkdir()
    shutil.copy(lib / "x.epub", lib / "dup" / "x.epub")
    _scan(env)
    root = etree.fromstring(client.get("/opds/books", auth=auth).content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    acq = [l for l in root.iter("{http://www.w3.org/2005/Atom}link") if l.get("rel") == "http://opds-spec.org/acquisition"]
    assert len(acq) == 1
    # 数据库里两份文件都在，详情页仍可见
    d = client.get(f"/api/books/{_uuid('Copyable')}", auth=auth).json()
    assert len(d["files"]) == 2
