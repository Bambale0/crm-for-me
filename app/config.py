"""Application configuration loaded from environment via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    owner_telegram_id: int = Field(default=0, alias="OWNER_TELEGRAM_ID")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://crm:crm@localhost:5432/crm",
        alias="DATABASE_URL",
    )
    alembic_database_url: str = Field(
        default="postgresql+asyncpg://crm:crm@localhost:5432/crm",
        alias="ALEMBIC_DATABASE_URL",
    )

    # Behaviour
    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")
    default_currency: str = Field(default="RUB", alias="DEFAULT_CURRENCY")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token) and self.owner_telegram_id > 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
