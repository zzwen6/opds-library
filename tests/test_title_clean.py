"""文件名 / 脏内嵌标题清洗：Kindle电子书:《三体》.mobi 这类文件刮不出来的回归测试。"""

from __future__ import annotations

import os
import time

import pytest

from app.db import SessionLocal
from app.models import Book, BookFile
from app.scan import scan_library
from app.scrapers.base import Candidate
from app.scrapers.service import scrape_book
from app.services.books import lock_field
from app.title_clean import clean_title, is_dirty_title, query_titles
from app.util import parse_filename_hint
from tests.conftest import make_epub, make_mobi


@pytest.mark.parametrize(
    "filename, title, author",
    [
        ("Kindle电子书:《三体》.mobi", "三体", None),
        ("Kindle电子书：《三体》.mobi", "三体", None),
        ("Kindle电子书三体.azw3", "三体", None),
        ("电子书：三体.epub", "三体", None),
        ("《三体》刘慈欣.epub", "三体", "刘慈欣"),
        ("刘慈欣《三体》.epub", "三体", "刘慈欣"),
        ("《三体》作者：刘慈欣.mobi", "三体", "刘慈欣"),
        ("《三体》（刘慈欣 著）.mobi", "三体", "刘慈欣"),
        ("《三体（精校版）》Kindle电子书.mobi", "三体", None),
        ("【精校版】三体2：黑暗森林(epub).epub", "三体2:黑暗森林", None),
        ("三体2：黑暗森林.epub", "三体2:黑暗森林", None),
        ("三体 - 刘慈欣.epub", "三体", "刘慈欣"),
        ("刘慈欣 - 三体.epub", "三体", "刘慈欣"),
        ("三体_刘慈欣.epub", "三体", "刘慈欣"),
        ("围城_钱锺书_epub.epub", "围城", "钱锺书"),
        ("[刘慈欣]三体.epub", "三体", "刘慈欣"),
        ("三体(作者:刘慈欣).epub", "三体", "刘慈欣"),
        ("三体（精校版）.epub", "三体", None),
        ("三体（全三册）.epub", "三体", None),
        ("三体\u3000全本.txt.epub", "三体", None),
        ("活着 余华 epub.epub", "活着", "余华"),
        ("[美]卡勒德·胡赛尼 - 追风筝的人.epub", "追风筝的人", "卡勒德·胡赛尼"),
        ("追风筝的人 - [美]卡勒德·胡赛尼.epub", "追风筝的人", "卡勒德·胡赛尼"),
        ("欧阳修 - 醉翁亭记.epub", "醉翁亭记", "欧阳修"),
        ("三体 - z-library.epub", "三体", None),
        ("www.sobooks.cc 三体.epub", "三体", None),
        ("9787536692930 三体.epub", "三体", None),
        ("Kindle版 三体 azw3.azw3", "三体", None),
        ("Jane Austen - Pride and Prejudice.epub", "Pride and Prejudice", "Jane Austen"),
        ("Pride and Prejudice (Penguin Classics).epub", "Pride and Prejudice", None),
        ("Dune: Messiah.epub", "Dune: Messiah", None),
        # 不该被误伤的
        ("完整的人.epub", "完整的人", None),
        ("三体全集.epub", "三体全集", None),
        ("Kindleberger.pdf", "Kindleberger", None),
        ("1984.epub", "1984", None),
    ],
)
def test_parse_filename_hint_variants(filename, title, author):
    got_title, got_author = parse_filename_hint(filename)
    assert got_title == title
    assert got_author == author


def test_query_titles_fall_back_to_shorter_forms():
    assert query_titles("Kindle电子书:《三体》") == ["三体"]
    assert query_titles("三体2：黑暗森林") == ["三体2:黑暗森林", "三体2", "三体"]
    assert query_titles("人类简史：从动物到上帝") == ["人类简史:从动物到上帝", "人类简史"]
    # 看起来正常的西文标题先按原样查
    assert query_titles("Sapiens - A Brief History of Humankind")[0] == "Sapiens - A Brief History of Humankind"
    assert query_titles(None) == []


def test_dirty_title_detection():
    assert is_dirty_title("Kindle电子书:《三体》")
    assert is_dirty_title("《活着》")
    assert is_dirty_title("活着（精校版）")
    assert not is_dirty_title("三体")
    assert not is_dirty_title("三体2：黑暗森林")
    assert not is_dirty_title("Pride and Prejudice")
    assert not is_dirty_title("Jane Austen - Pride and Prejudice")
    assert clean_title("Kindle电子书").title == "Kindle电子书"  # 全是杂词时保留原文


def _scan(env):
    with SessionLocal() as s:
        scan_library(s, env["library"])
        s.commit()


def _book_for(rel_path):
    with SessionLocal() as s:
        bf = s.query(BookFile).filter(BookFile.rel_path == rel_path).one()
        book = bf.book
        return book.uuid, book.title, [ba.author.name for ba in book.authors]


def test_scan_unparseable_mobi_uses_cleaned_filename(env):
    (env["library"] / "Kindle电子书:《三体》.mobi").write_bytes(b"not really a mobi")
    _scan(env)
    _, title, authors = _book_for("Kindle电子书:《三体》.mobi")
    assert title == "三体"
    assert authors == []


def test_scan_real_mobi_with_junk_embedded_title(env):
    make_mobi(env["library"] / "Kindle电子书:《三体》.mobi", "Kindle电子书:《三体》", "刘慈欣")
    _scan(env)
    _, title, authors = _book_for("Kindle电子书:《三体》.mobi")
    assert title == "三体"
    assert authors == ["刘慈欣"]
    with SessionLocal() as s:
        bf = s.query(BookFile).one()
        assert bf.extracted_title == "Kindle电子书:《三体》"  # 原始提取值仍保留


def test_scan_keeps_clean_embedded_title(env):
    # 文件名很脏，但内嵌标题干净：用内嵌标题
    make_mobi(env["library"] / "Kindle电子书:《随便》.mobi", "三体2：黑暗森林", "刘慈欣")
    make_epub(env["library"] / "Kindle电子书 - xx.epub", "Jane Austen - Pride and Prejudice", "Jane Austen")
    _scan(env)
    assert _book_for("Kindle电子书:《随便》.mobi")[1] == "三体2:黑暗森林"
    assert _book_for("Kindle电子书 - xx.epub")[1] == "Jane Austen - Pride and Prejudice"


def test_rescan_changed_file_respects_title_lock(env):
    path = make_epub(env["library"] / "a.epub", "《三体》", "刘慈欣")
    _scan(env)
    uuid, title, _ = _book_for("a.epub")
    assert title == "三体"
    with SessionLocal() as s:
        book = s.query(Book).filter(Book.uuid == uuid).one()
        book.title = "我的三体"
        lock_field(s, book, "title")
        s.commit()
    make_epub(path, "Kindle电子书:《三体》 精校版", "刘慈欣")
    st = path.stat()
    os.utime(path, (st.st_atime, time.time() + 5))
    _scan(env)
    assert _book_for("a.epub")[1] == "我的三体"


def test_rescan_changed_file_cleans_unlocked_title(env):
    path = make_epub(env["library"] / "a.epub", "三体", "刘慈欣")
    _scan(env)
    make_epub(path, "Kindle电子书:《三体》 精校版", "刘慈欣", description="changed")
    st = path.stat()
    os.utime(path, (st.st_atime, time.time() + 5))
    _scan(env)
    assert _book_for("a.epub")[1] == "三体"


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
            return [Candidate(source="fake", source_id="2567698", title="三体", authors=["刘慈欣"], language="zh")]
        return []


def _book_with_title(raw_title: str) -> str:
    with SessionLocal() as s:
        book = Book(title=raw_title, scrape_status="pending")
        s.add(book)
        s.commit()
        return book.uuid


def test_scrape_uses_cleaned_query_for_junk_title(env, monkeypatch):
    _RecordingScraper.queries = []
    _RecordingScraper.answer_for = "三体"
    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [_RecordingScraper])
    # 模拟 0.5.1 已入库的脏标题书
    uuid = _book_with_title("Kindle电子书:《三体》")
    with SessionLocal() as s:
        book = s.query(Book).filter(Book.uuid == uuid).one()
        cands = scrape_book(s, book)
        s.commit()
    assert _RecordingScraper.queries == ["三体"]
    assert cands and cands[0].title == "三体"
    assert cands[0].confidence >= 70


def test_scrape_retries_with_shorter_title(env, monkeypatch):
    _RecordingScraper.queries = []
    _RecordingScraper.answer_for = "三体2"
    monkeypatch.setattr("app.scrapers.service.list_scrapers", lambda: [_RecordingScraper])
    uuid = _book_with_title("【精校版】三体2：黑暗森林")
    with SessionLocal() as s:
        book = s.query(Book).filter(Book.uuid == uuid).one()
        cands = scrape_book(s, book)
        s.commit()
    assert _RecordingScraper.queries == ["三体2:黑暗森林", "三体2"]
    assert cands
