"""Adapter that maps an aiogram Message to forward-sender info.

Keeps aiogram types out of the business services; this module is the only
boundary that knows about Telegram message shapes.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import (
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
)


@dataclass(frozen=True)
class ForwardInfo:
    telegram_user_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    sender_name: str | None
    has_usable_id: bool

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        name = " ".join(parts).strip()
        return name or self.sender_name or "Без имени"


def extract_forward_info(message: Message) -> ForwardInfo | None:
    """Return sender info for a forwarded message, or None if not a forward."""
    origin = message.forward_origin
    if origin is None:
        return None

    if isinstance(origin, MessageOriginUser):
        user = origin.sender_user
        return ForwardInfo(
            telegram_user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            sender_name=user.full_name,
            has_usable_id=True,
        )

    if isinstance(origin, MessageOriginHiddenUser):
        return ForwardInfo(
            telegram_user_id=None,
            username=None,
            first_name=None,
            last_name=None,
            sender_name=origin.sender_user_name,
            has_usable_id=False,
        )

    if isinstance(origin, (MessageOriginChannel, MessageOriginChat)):
        chat = origin.chat if isinstance(origin, MessageOriginChannel) else origin.sender_chat
        sender_name = getattr(chat, "title", None) or getattr(chat, "full_name", None)
        return ForwardInfo(
            telegram_user_id=None,
            username=None,
            first_name=None,
            last_name=None,
            sender_name=sender_name,
            has_usable_id=False,
        )

    return ForwardInfo(None, None, None, None, None, False)
