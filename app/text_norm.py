"""用于作者归并与重复检测的文本规范化（处理中文、全角、标点、括注）。"""

from __future__ import annotations

import re
import unicodedata

# 开头的国籍/朝代括注：[美]、（英）、〔日〕、【清】、(美)
_NATION_PREFIX = re.compile(r"^\s*[\[\(（〔【［][^\]\)）〕】］]{1,6}[\]\)）〕】］]\s*")
# 书名里的括注：（精校版）、[epub]、【完结】等
_BRACKETED = re.compile(r"[\(（\[［【〔][^\)）\]］】〕]*[\)）\]］】〕]")
_TITLE_QUOTES = re.compile(r"[《》〈〉「」『』“”\"'‘’]")
_SUBTITLE_SPLIT = re.compile(r"\s*[:：—–]\s*|\s+-\s+")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_ASCII_TOKEN = re.compile(r"^[a-z0-9]+$")
_FORMAT_NOISE = re.compile(r"\b(epub|mobi|azw3|pdf|txt|cbz)\b", re.IGNORECASE)


def _nfkc(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def author_key(name: str | None) -> str:
    """作者匹配键：去国籍括注、全角转半角、忽略大小写/空格/标点；西文按词排序（兼容“姓, 名”）。"""
    text = _nfkc(name)
    text = _NATION_PREFIX.sub("", text)
    text = text.replace("·", " ").replace("•", " ").replace("・", " ").replace("‧", " ")
    tokens = [t for t in _NON_WORD.split(text) if t]
    if not tokens:
        return ""
    if all(_ASCII_TOKEN.match(t) for t in tokens):
        return "".join(sorted(tokens))
    return "".join(tokens)


def title_key(title: str | None) -> str:
    """完整书名匹配键：去书名号、括注、格式词和全部标点空白。"""
    text = _nfkc(title)
    text = _TITLE_QUOTES.sub("", text)
    text = _BRACKETED.sub(" ", text)
    text = _FORMAT_NOISE.sub(" ", text)
    return _NON_WORD.sub("", text)


def main_title_key(title: str | None) -> str:
    """主书名匹配键：去掉冒号/破折号后的副标题。"""
    text = _nfkc(title)
    text = _TITLE_QUOTES.sub("", text)
    text = _BRACKETED.sub(" ", text)
    head = _SUBTITLE_SPLIT.split(text, maxsplit=1)[0]
    head = _FORMAT_NOISE.sub(" ", head)
    return _NON_WORD.sub("", head)
