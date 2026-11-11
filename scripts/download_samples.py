#!/usr/bin/env python3
"""从 Project Gutenberg 下载若干公有领域 EPUB 作为样书。"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

# 书名仅作本地文件名；内容来自 Gutenberg 公有领域目录。
SAMPLES = {
    "pg1342-pride-and-prejudice.epub": "https://www.gutenberg.org/ebooks/1342.epub.images",
    "pg84-frankenstein.epub": "https://www.gutenberg.org/ebooks/84.epub.images",
    "pg11-alice.epub": "https://www.gutenberg.org/ebooks/11.epub.images",
}


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "library")
    dest.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "opds-library/0.1 (sample downloader; personal use)"}
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
        for name, url in SAMPLES.items():
            path = dest / name
            if path.exists() and path.stat().st_size > 1000:
                print(f"skip {name}")
                continue
            print(f"download {name} <- {url}")
            resp = client.get(url)
            resp.raise_for_status()
            path.write_bytes(resp.content)
            print(f"  saved {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
