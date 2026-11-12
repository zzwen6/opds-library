"""网页上传图书：入库 inbox/ 并自动刮削。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import SessionLocal
from app.models import Book, BookFile
from app.scrapers.base import Candidate
from app.services.upload import wait_upload_scrape
from tests.conftest import make_epub


@pytest.fixture(autouse=True)
def _quiet_scrapers(monkeypatch):
    """默认不上网；需要验证刮削的测试再自行 mock list_scrapers。"""
    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [])



class _RecordingScraper:
    id = "fake"
    display_name = "Fake"
    queries: list[str] = []
    answer_for: str = "三体"

    def lookup_isbn(self, isbn):  # noqa: ARG002
        return []

    def search(self, title, author=None, language=None):  # noqa: ARG002
        type(self).queries.append(title)
        if title == type(self).answer_for:
            return [
                Candidate(
                    source="fake",
                    source_id="2567698",
                    title="三体",
                    authors=["刘慈欣"],
                    language="zh",
                )
            ]
        return []


def _epub_bytes(dirpath: Path, title: str, author: str, name: str = "src.epub") -> bytes:
    path = make_epub(dirpath / name, title, author)
    return path.read_bytes()


def test_upload_requires_auth(client):
    resp = client.post(
        "/api/books/upload",
        files={"files": ("book.epub", b"not-a-real-epub", "application/epub+zip")},
    )
    assert resp.status_code == 401


def test_upload_rejects_bad_extension(client, auth):
    resp = client.post(
        "/api/books/upload",
        files={"files": ("notes.txt", b"hello", "text/plain")},
        auth=auth,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["saved"] == 0
    assert "不支持的文件格式" in data["items"][0]["error"]
    assert "epub" in data["items"][0]["error"]


def test_upload_rejected_when_library_not_writable(client, auth, monkeypatch):
    monkeypatch.setattr("app.services.upload.library_is_writable", lambda root=None: False)
    resp = client.post(
        "/api/books/upload",
        files={"files": ("book.epub", b"fake", "application/epub+zip")},
        auth=auth,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "不可写" in detail
    assert ":ro" in detail
    assert "ORGANIZE_ENABLED" in detail


def test_upload_does_not_require_organize_enabled(env, client, auth):
    # conftest 默认 ORGANIZE_ENABLED=false
    data = _epub_bytes(env["data"], "围城", "钱锺书", "weicheng.epub")
    resp = client.post(
        "/api/books/upload",
        files={"files": ("围城.epub", data, "application/epub+zip")},
        auth=auth,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert (env["library"] / "inbox" / "围城.epub").is_file()
    with SessionLocal() as session:
        book = session.query(Book).one()
        assert book.title == "围城"


def test_upload_happy_path_cleans_title_and_auto_scrapes(env, client, auth, monkeypatch):
    _RecordingScraper.queries = []
    _RecordingScraper.answer_for = "三体"
    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [_RecordingScraper])
    raw = _epub_bytes(env["data"], "Kindle电子书:《三体》", "刘慈欣", "santi.epub")
    resp = client.post(
        "/api/books/upload",
        files={"files": ("Kindle电子书:《三体》.epub", raw, "application/epub+zip")},
        auth=auth,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["scrape_started"] is True
    item = body["items"][0]
    assert item["ok"] is True
    assert item["title"] == "三体"
    assert item["scrape_status"] == "pending"
    assert item["rel_path"].startswith("inbox/")
    dest = env["library"] / item["rel_path"]
    assert dest.is_file()
    assert dest.read_bytes() == raw
    uuid = item["book_uuid"]
    assert uuid

    wait_upload_scrape()
    detail = client.get(f"/api/books/{uuid}", auth=auth)
    assert detail.status_code == 200
    book = detail.json()
    assert book["title"] == "三体"
    assert book["scrape_status"] == "auto_applied"
    assert "刘慈欣" in book["authors"]
    assert "三体" in _RecordingScraper.queries
    assert not any("Kindle" in q for q in _RecordingScraper.queries)


def test_upload_name_conflict_uses_numeric_suffix(env, client, auth):
    raw = _epub_bytes(env["data"], "活着", "余华", "huozhe.epub")
    first = client.post(
        "/api/books/upload",
        files={"files": ("活着.epub", raw, "application/epub+zip")},
        auth=auth,
    )
    second = client.post(
        "/api/books/upload",
        files={"files": ("活着.epub", raw, "application/epub+zip")},
        auth=auth,
    )
    assert first.status_code == 200 and second.status_code == 200
    a, b = first.json()["items"][0], second.json()["items"][0]
    assert a["rel_path"] == "inbox/活着.epub"
    assert b["rel_path"] == "inbox/活着 (2).epub"
    assert (env["library"] / "inbox" / "活着.epub").is_file()
    assert (env["library"] / "inbox" / "活着 (2).epub").is_file()
    assert b["possible_duplicate"] is True
    with SessionLocal() as session:
        assert session.query(BookFile).count() == 2


def test_upload_sanitizes_path_traversal_filename(env, client, auth):
    raw = _epub_bytes(env["data"], "边城", "沈从文", "biancheng.epub")
    resp = client.post(
        "/api/books/upload",
        files={"files": ("../../etc/passwd.epub", raw, "application/epub+zip")},
        auth=auth,
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["ok"] is True
    assert item["rel_path"] == "inbox/passwd.epub"
    assert (env["library"] / "inbox" / "passwd.epub").is_file()
    assert not (env["library"].parent / "etc").exists()
    # 不得写出书库之外
    for path in env["library"].rglob("*.epub"):
        path.resolve().relative_to(env["library"].resolve())


def test_upload_rejects_oversize(env, client, auth, monkeypatch):
    monkeypatch.setattr("app.services.upload.MAX_UPLOAD_BYTES", 64)
    raw = _epub_bytes(env["data"], "小书", "作者", "small.epub")
    assert len(raw) > 64
    resp = client.post(
        "/api/books/upload",
        files={"files": ("小书.epub", raw, "application/epub+zip")},
        auth=auth,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "过大" in data["items"][0]["error"]
    inbox = env["library"] / "inbox"
    leftovers = list(inbox.glob("*.epub")) if inbox.exists() else []
    assert leftovers == []
