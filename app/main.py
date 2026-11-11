from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import api_router
from app.auth import require_basic  # noqa: F401
from app.config import get_settings
from app.db import Base, engine, ensure_data_dirs
from app.opds.routes import router as opds_router

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app() -> FastAPI:
    settings = get_settings()
    ensure_data_dirs()
    Base.metadata.create_all(bind=engine)

    app = FastAPI(title="OPDS Library", version=__version__, docs_url="/api/docs", redoc_url=None)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    app.include_router(api_router, prefix="/api")
    app.include_router(opds_router)

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
