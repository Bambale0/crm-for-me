"""BillingPeriod, InvoiceItem and Payment repositories."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingPeriod, InvoiceItem, Payment


class BillingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, period_id: int) -> BillingPeriod | None:
        return await self.session.get(BillingPeriod, period_id)

    async def get_for_client_month(
        self, client_id: int, year: int, month: int
    ) -> BillingPeriod | None:
        stmt = select(BillingPeriod).where(
            BillingPeriod.client_id == client_id,
            BillingPeriod.year == year,
            BillingPeriod.month == month,
            BillingPeriod.status == "DRAFT",
        )
        return await self.session.scalar(stmt)

    async def list_for_client(self, client_id: int) -> list[BillingPeriod]:
        stmt = (
            select(BillingPeriod)
            .where(BillingPeriod.client_id == client_id)
            .order_by(BillingPeriod.created_at.desc(), BillingPeriod.id.desc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def add(self, period: BillingPeriod) -> BillingPeriod:
        self.session.add(period)
        await self.session.flush()
        return period

    async def list_for_month(self, year: int, month: int) -> list[BillingPeriod]:
        stmt = (
            select(BillingPeriod)
            .where(BillingPeriod.year == year, BillingPeriod.month == month)
            .order_by(BillingPeriod.id)
        )
        return list((await self.session.scalars(stmt)).all())

    async def list_items(self, period_id: int) -> list[InvoiceItem]:
        stmt = (
            select(InvoiceItem)
            .where(InvoiceItem.billing_period_id == period_id)
            .order_by(InvoiceItem.id)
        )
        return list((await self.session.scalars(stmt)).all())

    async def find_item_by_source(
        self, period_id: int, source_type: str, source_id: int
    ) -> InvoiceItem | None:
        stmt = select(InvoiceItem).where(
            InvoiceItem.billing_period_id == period_id,
            InvoiceItem.source_type == source_type,
            InvoiceItem.source_id == source_id,
        )
        return await self.session.scalar(stmt)

    async def add_item(self, item: InvoiceItem) -> InvoiceItem:
        self.session.add(item)
        await self.session.flush()
        return item

    async def add_payment(self, payment: Payment) -> Payment:
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def invoice_total(self, period_id: int) -> Decimal:
        stmt = select(func.coalesce(func.sum(InvoiceItem.amount), 0)).where(
            InvoiceItem.billing_period_id == period_id
        )
        return await self.session.scalar(stmt) or Decimal("0")

    async def paid_total(self, period_id: int) -> Decimal:
        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.billing_period_id == period_id
        )
        return await self.session.scalar(stmt) or Decimal("0")

    async def list_payments(self, period_id: int) -> list[Payment]:
        stmt = (
            select(Payment)
            .where(Payment.billing_period_id == period_id)
            .order_by(Payment.paid_at, Payment.id)
        )
        return list((await self.session.scalars(stmt)).all())
