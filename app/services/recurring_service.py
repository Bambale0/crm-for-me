"""Recurring charge service."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from ipaddress import ip_address

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingPeriod
from app.models.enums import BillingStatus, RecurringFrequency
from app.models.recurring import RecurringCharge
from app.repositories.recurring import RecurringRepository
from app.services.billing_service import BillingService
from app.services.errors import NotFoundError
from app.services.validation import lock_client, validate_project
from app.utils.money import to_decimal, validate_currency


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
        server_ip: str | None = None,
    ) -> RecurringCharge:
        if server_ip is not None:
            server_ip = self.normalize_ip(server_ip)
            title = f"Сервер {server_ip}"
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        await validate_project(self.session, client_id, project_id)
        if active_until is not None and active_until < active_from:
            raise ValueError("Дата окончания раньше даты начала")
        charge = RecurringCharge(
            client_id=client_id,
            project_id=project_id,
            server_ip=server_ip,
            title=title,
            amount=to_decimal(amount),
            currency=validate_currency(currency),
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

    async def update_amount(
        self, charge: RecurringCharge, amount: Decimal | str
    ) -> RecurringCharge:
        await lock_client(self.session, charge.client_id)
        charge.amount = to_decimal(amount)
        await self.session.flush()
        await self._reconcile_affected_drafts(charge)
        return charge

    async def deactivate(self, charge: RecurringCharge) -> RecurringCharge:
        await lock_client(self.session, charge.client_id)
        charge.is_active = False
        await self.session.flush()
        await self._reconcile_affected_drafts(charge)
        return charge

    async def list_for_client(
        self, client_id: int, include_inactive: bool = False
    ) -> list[RecurringCharge]:
        return await self.repo.list_for_client(client_id, include_inactive=include_inactive)

    async def _reconcile_affected_drafts(self, charge: RecurringCharge) -> None:
        stmt = select(BillingPeriod).where(
            BillingPeriod.client_id == charge.client_id,
            BillingPeriod.status == BillingStatus.DRAFT.value,
        )
        periods = list((await self.session.scalars(stmt)).all())
        for period in periods:
            await self.billing.reconcile_draft(period)

    @staticmethod
    def normalize_ip(value: str) -> str:
        try:
            value = value.strip()
            if "%" in value:
                raise ValueError
            return str(ip_address(value))
        except ValueError:
            raise ValueError("Введите корректный IPv4 или IPv6, например 192.0.2.10") from None

    async def update_server_ip(self, charge: RecurringCharge, value: str) -> RecurringCharge:
        ip = self.normalize_ip(value)
        await lock_client(self.session, charge.client_id)
        await self.session.refresh(charge)
        if charge.server_ip is None or not charge.is_active:
            raise ValueError("Откройте активный сервер")
        charge.server_ip = ip
        charge.title = f"Сервер {ip}"
        await self.session.flush()
        await self._reconcile_affected_drafts(charge)
        return charge

    async def list_servers(self, client_id: int | None = None) -> list[RecurringCharge]:
        return await self.repo.list_servers(client_id)
