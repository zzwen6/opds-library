from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import api_router
from app.auth import require_basic  # noqa: F401
from app.config import get_settings
from app.db import ensure_data_dirs, upgrade_schema
from app.opds.routes import router as opds_router
from app.opds2.routes import OPDS2AuthRequired, auth_required_response
from app.opds2.routes import public_router as opds2_public_router
from app.opds2.routes import router as opds2_router
from app.scan.watcher import start_watcher, stop_watcher

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    upgrade_schema()
    start_watcher()
    try:
        yield
    finally:
        stop_watcher()


def create_app() -> FastAPI:
    ensure_data_dirs()
    upgrade_schema()

    app = FastAPI(
        title="OPDS Library",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    app.include_router(api_router, prefix="/api")
    app.include_router(opds_router)
    app.include_router(opds2_public_router)
    app.include_router(opds2_router)

    @app.exception_handler(OPDS2AuthRequired)
    async def _opds2_auth_required(request, _exc):
        return auth_required_response(request)

    if WEB_DIST.exists():
        assets = WEB_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            candidate = WEB_DIST / full_path
            if full_path and candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            index = WEB_DIST / "index.html"
            if index.exists():
                return FileResponse(index)
            return JSONResponse({"detail": "前端尚未构建"}, status_code=404)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
