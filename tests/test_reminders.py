from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db
import app.worker
from app.services.reminder_service import ReminderService
from app.utils.time import now_utc


async def test_due_reminder_delivers_to_owner_once_and_future_waits(engine, monkeypatch):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app.db, "session_factory", factory)
    monkeypatch.setattr(app.worker, "get_settings", lambda: SimpleNamespace(owner_telegram_id=111))
    async with factory.begin() as s:
        await ReminderService(s).create("Due <text>", now_utc() - timedelta(minutes=1))
        await ReminderService(s).create("Future", now_utc() + timedelta(days=1))
    bot = AsyncMock()
    await app.worker.deliver_due(bot, 111)
    await app.worker.deliver_due(bot, 111)
    bot.send_message.assert_awaited_once_with(111, "⏰ Напоминание #1\nDue &lt;text&gt;")


async def test_failed_delivery_remains_pending_for_retry(engine, monkeypatch):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app.db, "session_factory", factory)
    monkeypatch.setattr(app.worker, "get_settings", lambda: SimpleNamespace(owner_telegram_id=111))
    async with factory.begin() as s:
        await ReminderService(s).create("Retry", now_utc() - timedelta(minutes=1))
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("transport unavailable")
    with pytest.raises(RuntimeError):
        await app.worker.deliver_due(bot, 111)
    async with factory() as s:
        assert len(await ReminderService(s).list_due()) == 1


async def test_reminders_cannot_be_sent_to_client(monkeypatch):
    monkeypatch.setattr(app.worker, "get_settings", lambda: SimpleNamespace(owner_telegram_id=111))
    with pytest.raises(ValueError):
        await app.worker.deliver_due(AsyncMock(), 222)
