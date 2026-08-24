"""Standalone browser app exposing only ExamPool's PDF to HWP workflow."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, routes_pdf_hwp
from .paths import BASE_DIR, STATIC_DIR


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="PDF to HWP", lifespan=lifespan)


@app.middleware("http")
async def no_cache_assets(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith(("/static/", "/assets/")):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


app.include_router(routes_pdf_hwp.router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "pdf-hwp"}


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "pdf-hwp.html"))
