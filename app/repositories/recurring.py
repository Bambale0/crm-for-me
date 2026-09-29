"""RecurringCharge repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.recurring import RecurringCharge


class RecurringRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, charge_id: int) -> RecurringCharge | None:
        return await self.session.get(RecurringCharge, charge_id)

    async def add(self, charge: RecurringCharge) -> RecurringCharge:
        self.session.add(charge)
        await self.session.flush()
        return charge

    async def list_for_client(
        self, client_id: int, include_inactive: bool = False
    ) -> list[RecurringCharge]:
        stmt = (
            select(RecurringCharge)
            .where(RecurringCharge.client_id == client_id)
            .order_by(RecurringCharge.title)
        )
        if not include_inactive:
            stmt = stmt.where(RecurringCharge.is_active.is_(True))
        return list((await self.session.scalars(stmt)).all())

    async def list_servers(self, client_id: int | None = None) -> list[RecurringCharge]:
        stmt = (
            select(RecurringCharge)
            .where(RecurringCharge.server_ip.is_not(None))
            .options(joinedload(RecurringCharge.client))
            .order_by(RecurringCharge.is_active.desc(), RecurringCharge.id)
        )
        if client_id is not None:
            stmt = stmt.where(RecurringCharge.client_id == client_id)
        return list((await self.session.scalars(stmt)).all())
