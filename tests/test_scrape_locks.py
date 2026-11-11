import json

from app.db import SessionLocal
from app.models import Book, ScrapeRecord
from app.scan import scan_library
from app.scrapers.service import confirm_candidate
from app.services.books import BookUpdate, get_book_by_uuid, update_book
from tests.conftest import make_epub


def test_confirm_respects_field_locks(env):
    lib = env["library"]
    make_epub(lib / "book.epub", "Original Title", "Original Author")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
        book = session.query(Book).one()
        update_book(session, book, BookUpdate(title="手改标题"), auto_lock=True)
        session.commit()
        record = ScrapeRecord(
            book_id=book.id,
            source="openlibrary",
            source_id="OL123",
            confidence=90,
            raw_json=json.dumps(
                {
                    "title": "刮削标题",
                    "description": "来自网络的简介",
                    "authors": ["Network Author"],
                },
                ensure_ascii=False,
            ),
            status="candidate",
        )
        session.add(record)
        session.commit()
        rid = record.id
        uuid = book.uuid
        book = confirm_candidate(session, book, rid)
        session.commit()
        book = get_book_by_uuid(session, uuid)
        assert book.title == "手改标题"
        assert book.description == "来自网络的简介"
        authors = [ba.author.name for ba in book.authors]
        assert "Network Author" in authors
