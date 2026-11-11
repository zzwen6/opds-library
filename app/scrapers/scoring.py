from __future__ import annotations

import re
import unicodedata

from app.scrapers.base import Candidate
from app.util import normalize_isbn, normalize_space


def _norm(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _token_set(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t}


def title_similarity(a: str | None, b: str | None) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    ta, tb = _token_set(na), _token_set(nb)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def author_similarity(book_authors: list[str], cand_authors: list[str]) -> float:
    if not book_authors or not cand_authors:
        return 0.0
    scores = []
    for ba in book_authors:
        scores.append(max(title_similarity(ba, ca) for ca in cand_authors))
    return sum(scores) / len(scores)


def score_candidate(
    candidate: Candidate,
    *,
    title: str | None,
    authors: list[str],
    isbn13: str | None,
    language: str | None,
) -> float:
    score = 0.0
    cand_isbn, _ = normalize_isbn(candidate.isbn13 or candidate.isbn10)
    book_isbn, _ = normalize_isbn(isbn13)
    if cand_isbn and book_isbn and cand_isbn == book_isbn:
        score = 100.0
    else:
        t = title_similarity(title, candidate.title)
        a = author_similarity(authors, candidate.authors)
        if t >= 0.99 and a >= 0.99:
            score = 90.0
        elif t >= 0.8 and a >= 0.7:
            score = 70 + 15 * ((t + a) / 2 - 0.75)
        elif t >= 0.6:
            score = 60 + 20 * t * (0.5 + 0.5 * a)
        else:
            score = 40 * t + 20 * a

    if language and candidate.language:
        if _norm(language)[:2] == _norm(candidate.language)[:2]:
            score = min(100.0, score + 3)
    candidate.confidence = round(min(score, 100.0), 2)
    return candidate.confidence
