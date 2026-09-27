"""Bot entrypoint: config validation, logging, engine and polling loop."""

from __future__ import annotations

import asyncio
import logging
import logging.config
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.handlers import register_all_handlers
from app.middlewares.owner import OwnerOnlyMiddleware
from app.worker import run_worker


def setup_logging(level: str) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structured": {
                    "()": "app.logging_setup.JsonFormatter",
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
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dp.update.outer_middleware(OwnerOnlyMiddleware(settings.owner_telegram_id))
    register_all_handlers(dp)

    worker = None
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT billing_period_id FROM tasks LIMIT 0"))
            await connection.execute(text("SELECT idempotency_key FROM payments LIMIT 0"))
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="cancel", description="Отменить ввод"),
            ]
        )
        worker = asyncio.create_task(run_worker(bot))
        logger.info("Starting bot", extra={"owner_id": settings.owner_telegram_id})
        await dp.start_polling(bot)
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await dp.storage.close()
        await bot.session.close()
        await engine.dispose()
        logger.info("Bot stopped")


def cli() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    cli()
