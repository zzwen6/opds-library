from __future__ import annotations

import hashlib
from pathlib import Path

import xxhash


def hash_file(path: Path) -> tuple[str, str]:
    """返回 (sha256_hex, xxhash64_hex)。"""
    sha = hashlib.sha256()
    xxh = xxhash.xxh64()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
            xxh.update(chunk)
    return sha.hexdigest(), xxh.hexdigest()
