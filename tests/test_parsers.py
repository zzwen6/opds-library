from pathlib import Path

from app.parsers import parse_file
from tests.conftest import make_cbz, make_cover_bytes, make_epub, make_pdf


def test_parse_epub_extracts_metadata_and_cover(tmp_path: Path):
    path = make_epub(
        tmp_path / "book.epub",
        title="Pride and Prejudice",
        author="Jane Austen",
        isbn="9780141439518",
        description="A classic novel.",
        subject="Fiction",
        cover=make_cover_bytes(),
    )
    parsed = parse_file(path)
    assert parsed.title == "Pride and Prejudice"
    assert "Jane Austen" in parsed.authors
    assert parsed.isbn13 == "9780141439518"
    assert parsed.description
    assert parsed.cover_bytes
    assert len(parsed.cover_bytes) > 100


def test_parse_pdf_extracts_title_author(tmp_path: Path):
    path = make_pdf(tmp_path / "doc.pdf", "A Modest Proposal", "Jonathan Swift", isbn="9780140434699")
    parsed = parse_file(path)
    assert parsed.title == "A Modest Proposal"
    assert "Jonathan Swift" in " ".join(parsed.authors)
    assert parsed.page_count == 1
    assert parsed.isbn13 == "9780140434699"


def test_parse_cbz_reads_comicinfo_and_cover(tmp_path: Path):
    path = make_cbz(tmp_path / "comic.cbz", "Issue One", series="Example Comics")
    parsed = parse_file(path)
    assert parsed.title == "Issue One"
    assert parsed.series == "Example Comics"
    assert parsed.authors
    assert parsed.cover_bytes
