from fastapi import APIRouter, Depends

from app.api import books, covers, organize, scan, scrape, settings
from app.auth import require_basic

api_router = APIRouter(dependencies=[Depends(require_basic)])
api_router.include_router(books.router)
api_router.include_router(covers.router)
api_router.include_router(scan.router)
api_router.include_router(scrape.router)
api_router.include_router(organize.router)
api_router.include_router(settings.router)
