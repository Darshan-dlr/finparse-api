"""
Application configuration via pydantic-settings.
All values read from environment variables / .env file.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_name: str = "FinParse API"
    app_version: str = "1.0.0"
    environment: str = "development"
    log_level: str = "INFO"
    api_port: int = 8000

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str
    database_url_sync: str  # used by Alembic

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── File Storage ──────────────────────────────────────────────────────────
    storage_backend: str = "local"
    storage_path: str = "./uploads"

    # ── File Size Limits ──────────────────────────────────────────────────────
    max_pdf_size_mb: int = 50
    max_csv_size_mb: int = 25
    max_pdf_pages: int = 200
    max_csv_rows: int = 100_000

    # ── CSV Parser ────────────────────────────────────────────────────────────
    csv_chunk_size: int = 1_000
    csv_min_text_confidence: float = 0.3

    # ── PDF Parser ────────────────────────────────────────────────────────────
    pdf_min_text_per_page: int = 50  # chars below this → treat as scanned

    @property
    def max_pdf_size_bytes(self) -> int:
        return self.max_pdf_size_mb * 1024 * 1024

    @property
    def max_csv_size_bytes(self) -> int:
        return self.max_csv_size_mb * 1024 * 1024

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — call this everywhere."""
    return Settings()
