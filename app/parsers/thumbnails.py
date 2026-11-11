from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

THUMB_SIZE = 256


def save_cover_and_thumb(
    data: bytes,
    dest: Path,
    thumb_dest: Path,
) -> tuple[int, int, str]:
    """保存原图与缩略图，返回 (width, height, mime)。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    thumb_dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        width, height = img.size
        dest_suffix = dest.suffix.lower()
        if dest_suffix in {".jpg", ".jpeg"}:
            img.save(dest, format="JPEG", quality=90)
            mime = "image/jpeg"
        elif dest_suffix == ".png":
            img.save(dest, format="PNG")
            mime = "image/png"
        else:
            img.save(dest, format="JPEG", quality=90)
            mime = "image/jpeg"
        thumb = img.copy()
        thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
        thumb.save(thumb_dest, format="JPEG", quality=85)
        return width, height, mime
