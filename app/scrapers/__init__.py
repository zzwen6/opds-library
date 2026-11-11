from app.scrapers.base import Candidate, Scraper, get_scraper, list_scrapers, register

# 注册内置源
from app.scrapers import google_books as _google_books  # noqa: F401
from app.scrapers import openlibrary as _openlibrary  # noqa: F401

__all__ = ["Candidate", "Scraper", "get_scraper", "list_scrapers", "register"]
