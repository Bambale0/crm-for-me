"""Bot entrypoint: config validation, logging, engine and polling loop."""

from __future__ import annotations

import asyncio
import logging
import logging.config

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import get_settings
from app.db import engine
from app.handlers import register_all_handlers
from app.middlewares.owner import OwnerOnlyMiddleware


def setup_logging(level: str) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structured": {
                    "format": (
                        '{"time": "%(asctime)s", "level": "%(levelname)s", '
                        '"logger": "%(name)s", "message": "%(message)s"}'
                    ),
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "structured",
                }
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                "aiogram": {"level": level, "handlers": ["console"], "propagate": False},
                "sqlalchemy.engine": {"level": "WARNING"},
            },
        }
    )


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger("crm")

    if not settings.is_configured:
        logger.error("BOT_TOKEN and OWNER_TELEGRAM_ID must be set in the environment")
        raise SystemExit(1)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(OwnerOnlyMiddleware(settings.owner_telegram_id))
    register_all_handlers(dp)

    logger.info("Starting bot", extra={"owner_id": settings.owner_telegram_id})
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
