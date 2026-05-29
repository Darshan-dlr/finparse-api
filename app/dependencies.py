"""FastAPI dependency injectors."""
from collections.abc import AsyncGenerator
from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session per request, always closing on exit."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_current_user(x_api_key: str | None = Header(None, alias="X-API-Key")) -> str:
    """
    Placeholder dependency for authentication.
    Returns 'system' by default or the provided X-API-Key header.
    Throws 401 Unauthorized if the key is 'invalid'.
    """
    if not x_api_key:
        return "system"
    if x_api_key == "invalid":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )
    return x_api_key
