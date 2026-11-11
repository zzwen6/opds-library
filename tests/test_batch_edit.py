from app.db import SessionLocal
from app.models import Author, Book, EditBatch
from app.scan import scan_library
from app.services.books import get_book_by_uuid, lock_field
from app.text_norm import author_key
from tests.conftest import make_epub


def _setup(env, n=3):
    lib = env["library"]
    for i in range(n):
        make_epub(lib / f"b{i}.epub", f"Book {i}", "Old Author", subject="Fiction")
    with SessionLocal() as s:
        scan_library(s, lib)
        s.commit()
        return [b.uuid for b in s.query(Book).order_by(Book.id).all()]


def test_batch_preview_apply_skip_locked_and_undo(client, auth, env):
    uuids = _setup(env)
    with SessionLocal() as s:
        book = get_book_by_uuid(s, uuids[1])
        lock_field(s, book, "authors")
        s.commit()
    body = {
        "book_uuids": uuids,
        "ops": {
            "authors": {"mode": "replace", "values": ["新作者"]},
            "tags": {"mode": "add", "values": ["小说", "经典"]},
            "series": {"name": "测试系列", "index_start": 1},
            "language": "zh",
            "publisher": "人民文学出版社",
        },
    }
    prev = client.post("/api/batch/preview", json=body, auth=auth).json()
    assert prev["selected"] == 3
    assert prev["affected_books"] == 3
    assert prev["skipped_books"] == 1
    assert prev["items"][1]["skipped"][0]["field"] == "authors"
    # 预览不落库
    with SessionLocal() as s:
        assert s.query(EditBatch).count() == 0

    res = client.post("/api/batch/apply", json=body, auth=auth).json()
    assert res["affected_books"] == 3
    assert res["skipped"][0]["fields"] == ["authors"]
    batch_id = res["batch_id"]

    d0 = client.get(f"/api/books/{uuids[0]}", auth=auth).json()
    d1 = client.get(f"/api/books/{uuids[1]}", auth=auth).json()
    d2 = client.get(f"/api/books/{uuids[2]}", auth=auth).json()
    assert d0["authors"] == ["新作者"]
    assert d1["authors"] == ["Old Author"]  # 锁定字段未改
    assert set(d0["tags"]) >= {"小说", "经典", "Fiction"}
    assert d0["series"] == "测试系列" and d0["series_index"] == "1"
    assert d2["series_index"] == "3"
    assert d0["language"] == "zh" and d0["publisher"] == "人民文学出版社"
    assert d0["field_sources"]["authors"] == "batch"
    assert "authors" in {l["field_name"] for l in d0["locks"]}  # lock_after 默认加锁

    hist = client.get("/api/edit-batches", auth=auth).json()
    assert hist[0]["id"] == batch_id and hist[0]["kind"] == "batch_edit"

    undo = client.post(f"/api/edit-batches/{batch_id}/undo", json={}, auth=auth).json()
    assert undo["status"] == "undone" and not undo["conflicts"]
    d0 = client.get(f"/api/books/{uuids[0]}", auth=auth).json()
    assert d0["authors"] == ["Old Author"]
    assert d0["tags"] == ["Fiction"]
    assert d0["series"] is None and d0["series_index"] is None
    assert d0["publisher"] is None
    assert "authors" not in d0["field_sources"]
    assert "authors" not in {l["field_name"] for l in d0["locks"]}
    d1 = client.get(f"/api/books/{uuids[1]}", auth=auth).json()
    assert "authors" in {l["field_name"] for l in d1["locks"]}  # 用户原有的锁不动
    # 不能重复撤销
    assert client.post(f"/api/edit-batches/{batch_id}/undo", auth=auth).status_code == 400


def test_batch_undo_reports_conflict_when_edited_later(client, auth, env):
    uuids = _setup(env, 2)
    body = {"book_uuids": uuids, "ops": {"language": "fr"}, "lock_after": False}
    batch_id = client.post("/api/batch/apply", json=body, auth=auth).json()["batch_id"]
    client.patch(f"/api/books/{uuids[0]}", json={"language": "de"}, auth=auth)
    undo = client.post(f"/api/edit-batches/{batch_id}/undo", auth=auth).json()
    assert undo["status"] == "partially_undone"
    assert len(undo["conflicts"]) == 1
    assert client.get(f"/api/books/{uuids[0]}", auth=auth).json()["language"] == "de"
    assert client.get(f"/api/books/{uuids[1]}", auth=auth).json()["language"] == "en"


def test_tag_remove_and_replace(client, auth, env):
    uuids = _setup(env, 2)
    client.post(
        "/api/batch/apply",
        json={"book_uuids": uuids, "ops": {"tags": {"mode": "remove", "values": ["fiction"]}}, "lock_after": False},
        auth=auth,
    )
    assert client.get(f"/api/books/{uuids[0]}", auth=auth).json()["tags"] == []
    client.post(
        "/api/batch/apply",
        json={"book_uuids": uuids, "ops": {"tags": {"mode": "replace", "values": ["A", "B", "a"]}}},
        auth=auth,
    )
    assert client.get(f"/api/books/{uuids[1]}", auth=auth).json()["tags"] == ["A", "B"]


def test_author_key_normalization():
    assert author_key("[美]海明威") == author_key("海明威")
    assert author_key("（英）简·奥斯汀") == author_key("简奥斯汀")
    assert author_key("J.K. Rowling") == author_key("Rowling, J. K.")
    assert author_key("ＪＫ　Ｒｏｗｌｉｎｇ") == author_key("jk rowling")
    assert author_key("余 华") == author_key("余华")
    assert author_key("余华") != author_key("莫言")


def test_author_variants_and_merge_with_undo(client, auth, env):
    lib = env["library"]
    make_epub(lib / "a.epub", "活着", "余华")
    make_epub(lib / "b.epub", "兄弟", "余 华")
    make_epub(lib / "c.epub", "许三观卖血记", "[中]余华")
    make_epub(lib / "d.epub", "第七天", "余 华")
    with SessionLocal() as s:
        scan_library(s, lib)
        s.commit()
        locked = s.query(Book).filter(Book.title == "第七天").one()
        lock_field(s, locked, "authors")
        s.commit()
    variants = client.get("/api/authors/variants", auth=auth).json()
    assert len(variants) == 1
    names = {a["name"] for a in variants[0]["authors"]}
    assert names == {"余华", "余 华", "[中]余华"}

    req = {"sources": ["余 华", "[中]余华"], "target": "余华"}
    prev = client.post("/api/authors/merge/preview", json=req, auth=auth).json()
    assert prev["affected_books"] == 2 and prev["skipped_books"] == 1
    res = client.post("/api/authors/merge", json=req, auth=auth).json()
    assert res["affected_books"] == 2
    assert res["removed_authors"] == ["[中]余华"]  # “余 华”仍被锁定的书引用，保留
    with SessionLocal() as s:
        target = s.query(Author).filter(Author.name == "余华").one()
        assert "余 华" in (target.aliases_json or "")
    authors = {a["name"]: a["book_count"] for a in client.get("/api/authors", auth=auth).json()}
    assert authors["余华"] == 3 and authors["余 华"] == 1

    undo = client.post(f"/api/edit-batches/{res['batch_id']}/undo", auth=auth).json()
    assert undo["status"] == "undone"
    authors = {a["name"]: a["book_count"] for a in client.get("/api/authors", auth=auth).json()}
    assert authors["余华"] == 1 and authors["余 华"] == 2 and authors["[中]余华"] == 1


def test_batch_requires_auth(client):
    assert client.post("/api/batch/preview", json={"book_uuids": [], "ops": {}}).status_code == 401


def test_upgrade_schema_adds_new_tables_to_old_db(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.db import upgrade_schema

    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE books (id INTEGER PRIMARY KEY, uuid VARCHAR(36), title VARCHAR(1024))"))
        conn.execute(text("INSERT INTO books (id, uuid, title) VALUES (1, 'u1', '旧书')"))
    upgrade_schema(eng)
    tables = set(inspect(eng).get_table_names())
    assert {"edit_batches", "edit_changes", "duplicate_ignores"} <= tables
    with eng.connect() as conn:
        assert conn.execute(text("SELECT title FROM books WHERE id=1")).scalar() == "旧书"
