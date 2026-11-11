from __future__ import annotations

import re
import unicodedata
from pathlib import Path

ISBN13_RE = re.compile(r"(?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx]")
ISBN_CLEAN_RE = re.compile(r"[^0-9Xx]")

SUPPORTED_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".cbz"}
SKIP_DIR_NAMES = {
    "@eadir",
    ".ds_store",
    "#recycle",
    ".recycle",
    ".Trash",
    ".trashes",
    "__macosx",
    "lost+found",
    ".git",
}


def normalize_space(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def sort_key(value: str | None) -> str | None:
    text = normalize_space(value)
    if not text:
        return None
    return text.casefold()


def isbn_checksum_13(digits: str) -> bool:
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum((int(d) * (1 if i % 2 == 0 else 3)) for i, d in enumerate(digits[:-1]))
    check = (10 - (total % 10)) % 10
    return check == int(digits[-1])


def isbn_checksum_10(digits: str) -> bool:
    if len(digits) != 10:
        return False
    body, check = digits[:-1], digits[-1].upper()
    if not body.isdigit():
        return False
    total = sum(int(d) * (10 - i) for i, d in enumerate(body))
    check_val = (11 - (total % 11)) % 11
    expected = "X" if check_val == 10 else str(check_val)
    return check == expected


def isbn10_to_isbn13(isbn10: str) -> str | None:
    digits = ISBN_CLEAN_RE.sub("", isbn10).upper()
    if not isbn_checksum_10(digits):
        return None
    core = "978" + digits[:-1]
    total = sum((int(d) * (1 if i % 2 == 0 else 3)) for i, d in enumerate(core))
    check = (10 - (total % 10)) % 10
    return core + str(check)


def normalize_isbn(value: str | None) -> tuple[str | None, str | None]:
    """返回 (isbn13, isbn10)。无法识别则 (None, None)。"""
    if not value:
        return None, None
    digits = ISBN_CLEAN_RE.sub("", value).upper()
    if len(digits) == 13 and isbn_checksum_13(digits):
        isbn10 = None
        if digits.startswith("978"):
            core = digits[3:-1]
            total = sum(int(d) * (10 - i) for i, d in enumerate(core))
            check_val = (11 - (total % 11)) % 11
            isbn10 = core + ("X" if check_val == 10 else str(check_val))
        return digits, isbn10
    if len(digits) == 10 and isbn_checksum_10(digits):
        return isbn10_to_isbn13(digits), digits
    return None, None


def find_isbn(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    for match in ISBN13_RE.finditer(text):
        isbn13, isbn10 = normalize_isbn(match.group(0))
        if isbn13 or isbn10:
            return isbn13, isbn10
    compact = ISBN_CLEAN_RE.sub("", text)
    return normalize_isbn(compact)


def parse_filename_hint(filename: str) -> tuple[str | None, str | None]:
    """从「作者 - 书名」或「书名 - 作者」一类文件名里猜标题和作者（低置信度）。"""
    stem = Path(filename).stem
    stem = re.sub(r"[\[\(].*?[\]\)]", " ", stem)
    isbn13, _ = find_isbn(stem)
    if isbn13:
        stem = re.sub(re.escape(isbn13), " ", stem)
    stem = normalize_space(stem) or ""
    if " - " in stem:
        left, right = stem.split(" - ", 1)
        left, right = normalize_space(left), normalize_space(right)
        # 启发式：较短的一侧更像作者
        if left and right:
            if len(left) <= len(right):
                return right, left
            return left, right
    return normalize_space(stem), None


def should_skip_dir(name: str) -> bool:
    return name.startswith(".") or name.lower() in {n.lower() for n in SKIP_DIR_NAMES}


def format_from_path(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "azw":
        return "azw3"
    return ext
