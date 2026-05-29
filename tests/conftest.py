"""
pytest conftest — shared fixtures for all tests.
CSV parser tests don't need DB; no async setup needed for those.
"""
import pytest
from pathlib import Path

SAMPLE_FILES = Path(__file__).parent / "sample_files"


@pytest.fixture
def sample_files_dir() -> Path:
    return SAMPLE_FILES


@pytest.fixture
def standard_csv_bytes() -> bytes:
    return (SAMPLE_FILES / "standard_bank_statement.csv").read_bytes()


@pytest.fixture
def european_csv_bytes() -> bytes:
    return (SAMPLE_FILES / "european_semicolon_statement.csv").read_bytes()


@pytest.fixture
def hdfc_csv_bytes() -> bytes:
    return (SAMPLE_FILES / "hdfc_style_statement.csv").read_bytes()


# ── Database & FastAPI Test Infrastructure ────────────────────────────────────
import asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.dependencies import get_db
from app.models import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    policy = asyncio.get_event_loop_policy()
    res = policy.new_event_loop()
    asyncio.set_event_loop(res)
    yield res
    res.close()


@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
    # Create all tables in the test database
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Clean up tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session for each test."""
    connection = await test_engine.connect()
    transaction = await connection.begin()

    TestingSessionLocal = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    session = TestingSessionLocal()
    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest.fixture
async def async_client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """Return an AsyncClient for testing the FastAPI application."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()

