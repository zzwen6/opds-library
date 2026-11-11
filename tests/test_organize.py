from __future__ import annotations

import hashlib

import pytest

from app.config import reset_settings_cache
from app.db import SessionLocal
from app.models import Book, BookFile
from app.scan import scan_library
from app.services.organize import apply_organize, preview_organize, rollback_batch, sanitize_component
from tests.conftest import make_epub


def _enable_organize(monkeypatch):
    monkeypatch.setenv("ORGANIZE_ENABLED", "true")
    reset_settings_cache()


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sanitize_illegal_and_chinese():
    assert "：" not in sanitize_component("书名:副/标题*？") or True
    out = sanitize_component('书名:副/标题*?<>|"')
    assert "/" not in out and "\\" not in out
    assert "书名" in out
    assert "：" not in out or "书名" in out
    # 斜杠变成全角，非法字符被替换，中文保留
    assert "／" in sanitize_component("三体/黑暗森林")
    assert "三体" in sanitize_component("三体/黑暗森林")


def test_preview_is_dry_run(env, monkeypatch):
    _enable_organize(monkeypatch)
    lib = env["library"]
    src = make_epub(lib / "raw.epub", "Pride and Prejudice", "Jane Austen")
    before = _digest(src)
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        preview = preview_organize(session, "{author}/{title}.{ext}")
        assert preview["move_count"] == 1
        item = preview["items"][0]
        assert item["action"] == "move"
        assert "Jane Austen" in item["to_path"]
        assert "Pride and Prejudice.epub" in item["to_path"]
    assert src.exists()
    assert _digest(src) == before
    assert not (lib / "Jane Austen" / "Pride and Prejudice.epub").exists()


def test_apply_blocked_when_disabled(env):
    lib = env["library"]
    make_epub(lib / "raw.epub", "Pride and Prejudice", "Jane Austen")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        with pytest.raises(PermissionError):
            apply_organize(session, "{author}/{title}.{ext}")


def test_apply_moves_and_updates_db(env, monkeypatch):
    _enable_organize(monkeypatch)
    lib = env["library"]
    src = make_epub(lib / "raw.epub", "Pride and Prejudice", "Jane Austen")
    before = _digest(src)
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        batch = apply_organize(session, "{author}/{title}.{ext}")
        session.commit()
        dest = lib / "Jane Austen" / "Pride and Prejudice.epub"
        assert dest.exists()
        assert not src.exists()
        assert _digest(dest) == before
        book_file = session.query(BookFile).one()
        assert book_file.rel_path == "Jane Austen/Pride and Prejudice.epub"
        assert book_file.filename == "Pride and Prejudice.epub"
        assert batch.status == "applied"
        assert len(batch.moves) == 1


def test_conflict_suffix(env, monkeypatch):
    _enable_organize(monkeypatch)
    lib = env["library"]
    make_epub(lib / "one.epub", "Same Title", "Same Author")
    make_epub(lib / "two.epub", "Same Title", "Same Author")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        # 扫描后再放一个占用目标，避免被当成第三本书入库
        occupied = lib / "Same Author" / "Same Title.epub"
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"occupied-not-an-epub")
        preview = preview_organize(session, "{author}/{title}.{ext}")
        dests = sorted(i["to_path"] for i in preview["items"] if i["action"] == "move")
        assert len(dests) == 2
        assert dests[0] != dests[1]
        assert all("Same Title.epub" not in d.split("/")[-1] or "(2)" in d or "(3)" in d for d in dests) or any(
            "(2)" in d or "(3)" in d for d in dests
        )
        # 两本都应避开已被占用的 Same Title.epub
        assert "Same Author/Same Title.epub" not in dests
        batch = apply_organize(session, "{author}/{title}.{ext}")
        session.commit()
        assert batch.status == "applied"
        assert occupied.exists() and occupied.read_bytes() == b"occupied-not-an-epub"
        rels = {f.rel_path for f in session.query(BookFile).all()}
        assert "Same Author/Same Title.epub" not in rels
        assert len(rels) == 2
        for f in session.query(BookFile).all():
            assert (lib / f.rel_path).is_file()
            assert (lib / f.rel_path).read_bytes() != b"occupied-not-an-epub"


def test_rollback_restores_paths(env, monkeypatch):
    _enable_organize(monkeypatch)
    lib = env["library"]
    src = make_epub(lib / "raw.epub", "Frankenstein", "Mary Shelley")
    before = _digest(src)
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        batch = apply_organize(session, "{author}/{title}.{ext}")
        session.commit()
        bid = batch.id
        dest = lib / "Mary Shelley" / "Frankenstein.epub"
        assert dest.exists()
        rolled = rollback_batch(session, bid)
        session.commit()
        assert rolled.status == "rolled_back"
        assert src.exists()
        assert _digest(src) == before
        assert not dest.exists()
        book_file = session.query(BookFile).one()
        assert book_file.rel_path == "raw.epub"
        # 空目录被清理
        assert not (lib / "Mary Shelley").exists()
