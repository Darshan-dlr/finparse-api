"""Async SQLAlchemy engine and session factory."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

is_sqlite = settings.database_url.startswith("sqlite")

kwargs = {}
if is_sqlite:
    # SQLite-specific configuration
    kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL-specific pool configuration
    kwargs["pool_size"] = 10
    kwargs["max_overflow"] = 20
    kwargs["pool_pre_ping"] = True

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,   # Log SQL in dev mode
    **kwargs
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)
