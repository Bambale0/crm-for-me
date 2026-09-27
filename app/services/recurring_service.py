"""Recurring charge service."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingPeriod
from app.models.enums import BillingStatus, RecurringFrequency
from app.models.recurring import RecurringCharge
from app.repositories.recurring import RecurringRepository
from app.services.billing_service import BillingService, charge_active_in_month
from app.services.errors import NotFoundError
from app.utils.money import quantize


class RecurringService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = RecurringRepository(session)
        self.billing = BillingService(session)

    async def create(
        self,
        client_id: int,
        title: str,
        amount: Decimal | str,
        *,
        active_from: date,
        active_until: date | None = None,
        project_id: int | None = None,
        currency: str = "RUB",
    ) -> RecurringCharge:
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        charge = RecurringCharge(
            client_id=client_id,
            project_id=project_id,
            title=title,
            amount=quantize(Decimal(str(amount))),
            currency=currency,
            frequency=RecurringFrequency.MONTHLY.value,
            active_from=active_from,
            active_until=active_until,
            is_active=True,
        )
        await self.repo.add(charge)
        await self._reconcile_affected_drafts(charge)
        return charge

    async def get(self, charge_id: int) -> RecurringCharge:
        charge = await self.repo.get(charge_id)
        if charge is None:
            raise NotFoundError(f"RecurringCharge {charge_id} not found")
        return charge

    async def update_amount(self, charge: RecurringCharge, amount: Decimal | str) -> RecurringCharge:
        charge.amount = quantize(Decimal(str(amount)))
        await self.session.flush()
        await self._reconcile_affected_drafts(charge)
        return charge

    async def deactivate(self, charge: RecurringCharge) -> RecurringCharge:
        charge.is_active = False
        await self.session.flush()
        await self._reconcile_affected_drafts(charge)
        return charge

    async def list_for_client(self, client_id: int, include_inactive: bool = False) -> list[RecurringCharge]:
        return await self.repo.list_for_client(client_id, include_inactive=include_inactive)

    async def _reconcile_affected_drafts(self, charge: RecurringCharge) -> None:
        stmt = select(BillingPeriod).where(
            BillingPeriod.client_id == charge.client_id,
            BillingPeriod.status == BillingStatus.DRAFT.value,
        )
        periods = list((await self.session.scalars(stmt)).all())
        for period in periods:
            if charge_active_in_month(charge, period.year, period.month):
                await self.billing.reconcile_draft(period)
