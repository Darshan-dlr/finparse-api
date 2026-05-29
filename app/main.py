"""
FastAPI application entry point.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.core.exceptions import FinParseException
from app.core.logging import get_logger, setup_logging
from app.database import engine
from app.models.base import Base
import app.models  # noqa: F401 — registers all ORM models with Base.metadata
from app.api.v1.router import api_router

setup_logging()
logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting FinParse API", version=settings.app_version, env=settings.environment)
    # Create tables (use Alembic in production)
    if settings.is_development:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables verified / created")
        except Exception as e:
            logger.warning(
                "Database not available at startup — tables not created. "
                "Upload endpoints will fail until DB is running.",
                error=str(e),
            )
    yield
    logger.info("Shutting down FinParse API")
    await engine.dispose()



app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Production-grade Invoice & Bank Statement Parsing API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(FinParseException)
async def finparse_exception_handler(request: Request, exc: FinParseException) -> JSONResponse:
    logger.warning(
        "Request failed",
        error_code=exc.error_code,
        message=exc.message,
        path=request.url.path,
        detail=exc.detail,
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
    )

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "version": settings.app_version}
