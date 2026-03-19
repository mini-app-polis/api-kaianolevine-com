from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    ENVIRONMENT: str = "development"
    API_VERSION: str = "1.0"
    OWNER_ID: str = "dev-owner"
    SENTRY_DSN: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_current_owner(
    x_owner_id: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """
    Reads owner from X-Owner-Id request header.
    Falls back to settings.OWNER_ID if header not present.
    TODO: Replace with Clerk JWT verification before production.
    """
    return x_owner_id or settings.OWNER_ID
