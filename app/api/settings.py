from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.services.organize import library_is_writable

router = APIRouter(tags=["settings"])


@router.get("/settings")
def api_settings():
    settings = get_settings()
    return {
        "scrape_auto_threshold": settings.scrape_auto_threshold,
        "organize_enabled": settings.organize_enabled,
        "organize_template": settings.organize_template,
        "library_writable": library_is_writable(),
        "watch_enabled": settings.watch_enabled,
        "watch_poll_seconds": settings.watch_poll_seconds,
        "library_path": str(settings.library_path),
        "douban_enabled": settings.douban_enabled,
        "douban_api_url": settings.douban_api_url,
    }
