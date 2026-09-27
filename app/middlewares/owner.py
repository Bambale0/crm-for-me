"""Owner-only access control + structured logging middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

logger = logging.getLogger("crm.access")


class OwnerOnlyMiddleware(BaseMiddleware):
    """Drop any update that does not come from the configured owner."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = _extract_user_id(event)
        if user_id is None:
            logger.warning("Update without user id dropped", extra={"event": type(event).__name__})
            return None
        if user_id != self.owner_id:
            logger.warning("Unauthorized access attempt", extra={"user_id": user_id})
            return None
        inner = (event.message or event.callback_query) if isinstance(event, Update) else event
        message = inner.message if isinstance(inner, CallbackQuery) else inner
        if not isinstance(message, Message) or message.chat.type != "private":
            return None
        logger.debug("Owner update", extra={"user_id": user_id})
        return await handler(event, data)


def _extract_user_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.from_user.id if event.from_user else None
    if isinstance(event, CallbackQuery):
        return event.from_user.id if event.from_user else None
    if isinstance(event, Update):
        inner = event.message or event.callback_query
        if inner is not None and inner.from_user is not None:
            return inner.from_user.id
    return None
