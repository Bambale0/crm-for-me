import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from pydantic import ValidationError

import app.worker
from app.config import Settings
from app.handlers.forward import source_key
from app.logging_setup import JsonFormatter
from app.services.forward_resolver import extract_forward_info


def test_channel_forward_has_no_fake_user_and_stable_source():
    message = Message.model_validate(
        {
            "message_id": 1,
            "date": datetime.now(timezone.utc),
            "chat": {"id": 111, "type": "private"},
            "text": "Post",
            "forward_origin": {
                "type": "channel",
                "date": datetime.now(timezone.utc),
                "chat": {"id": -100222, "type": "channel", "title": "Channel"},
                "message_id": 5,
            },
        }
    )
    info = extract_forward_info(message)
    assert (info.telegram_user_id, info.display_name, source_key(message)) == (
        None,
        "Channel",
        "channel:-100222:5",
    )


def test_json_log_escapes_newlines_quotes_and_preserves_update_id():
    record = logging.LogRecord("crm", logging.ERROR, __file__, 1, 'line\n"quoted"', (), None)
    record.update_id = 123
    payload = json.loads(JsonFormatter().format(record))
    assert (payload["message"], payload["update_id"]) == ('line\n"quoted"', 123)


def test_configuration_rejects_invalid_timezone_and_mixed_currency():
    with pytest.raises(ValidationError):
        Settings(TIMEZONE="not-a-timezone", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(DEFAULT_CURRENCY="USD", _env_file=None)


async def test_worker_generates_month_and_exits_on_cancellation(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def session_scope():
        yield object()

    billing = SimpleNamespace(generate_month=AsyncMock())
    monkeypatch.setattr(
        app.worker,
        "get_settings",
        lambda: SimpleNamespace(
            timezone="UTC", owner_telegram_id=111, reminder_interval_seconds=30
        ),
    )
    monkeypatch.setattr(app.worker, "session_scope", session_scope)
    monkeypatch.setattr(app.worker, "BillingService", lambda session: billing)
    delivery = AsyncMock()
    monkeypatch.setattr(app.worker, "deliver_due", delivery)
    monkeypatch.setattr(app.worker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await app.worker.run_worker(AsyncMock())
    billing.generate_month.assert_awaited_once()
    delivery.assert_awaited_once()
