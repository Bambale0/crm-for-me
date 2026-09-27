"""Single-process owner reminder delivery and monthly recurring generation."""

import asyncio
import logging
from html import escape

from aiogram import Bot

from app.config import get_settings
from app.db import session_scope
from app.services.billing_service import BillingService
from app.services.reminder_service import ReminderService
from app.utils.time import current_month

logger = logging.getLogger("crm.worker")


async def deliver_due(bot: Bot, owner_id: int) -> int:
    if owner_id <= 0 or owner_id != get_settings().owner_telegram_id:
        raise ValueError("Reminder recipient must be the configured owner")
    count = 0
    async with session_scope() as session:
        svc = ReminderService(session)
        for reminder in await svc.list_due():
            await bot.send_message(
                owner_id, f"⏰ Напоминание #{reminder.id}\n{escape(reminder.text)}"
            )
            await svc.complete(reminder)
            count += 1
    return count


async def run_worker(bot: Bot) -> None:
    settings = get_settings()
    generated = None
    while True:
        try:
            month = current_month(settings.timezone)
            if generated != month:
                async with session_scope() as session:
                    await BillingService(session).generate_month(*month)
                generated = month
            await deliver_due(bot, settings.owner_telegram_id)
        except Exception as exc:
            logger.error("Background operation failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(settings.reminder_interval_seconds)
