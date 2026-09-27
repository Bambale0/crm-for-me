"""Reminder service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ReminderStatus
from app.models.reminder import Reminder
from app.utils.time import now_utc


class ReminderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        text: str,
        remind_at: datetime,
        *,
        client_id: int | None = None,
        task_id: int | None = None,
        billing_period_id: int | None = None,
    ) -> Reminder:
        reminder = Reminder(
            client_id=client_id,
            task_id=task_id,
            billing_period_id=billing_period_id,
            remind_at=remind_at,
            text=text,
            status=ReminderStatus.PENDING.value,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def list_due(self, now: datetime | None = None) -> list[Reminder]:
        now = now or now_utc()
        stmt = select(Reminder).where(
            Reminder.status == ReminderStatus.PENDING.value,
            Reminder.remind_at <= now,
        )
        return list((await self.session.scalars(stmt)).all())

    async def complete(self, reminder: Reminder) -> Reminder:
        reminder.status = ReminderStatus.DONE.value
        reminder.completed_at = now_utc()
        await self.session.flush()
        return reminder
