from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ops"
    jwt_secret: str = "dev-only-secret-change-me-in-production-please"
    jwt_ttl_minutes: int = 12 * 60
    idempotency_ttl_hours: int = 24
    outbox_max_attempts: int = 5
    cors_origins: str = "http://localhost:5173"


settings = Settings()
