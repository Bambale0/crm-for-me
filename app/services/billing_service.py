"""Billing service: periods, idempotent reconciliation, invoice snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingPeriod, InvoiceItem
from app.models.enums import BillingStatus, InvoiceItemSource, TaskStatus
from app.models.recurring import RecurringCharge
from app.models.task import Task
from app.repositories.billing import BillingRepository
from app.services.errors import InvalidTransitionError, NotFoundError
from app.utils.time import current_month, local_month, now_utc


@dataclass(frozen=True)
class Totals:
    invoice_total: Decimal
    paid_total: Decimal
    debt: Decimal


def charge_active_in_month(charge: RecurringCharge, year: int, month: int) -> bool:
    """Whether a monthly recurring charge applies to the given calendar month."""
    if not charge.is_active:
        return False
    month_start = date(year, month, 1)
    if month_start < charge.active_from:
        return False
    if charge.active_until is not None and month_start > charge.active_until:
        return False
    return True


class BillingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.billing = BillingRepository(session)

    # ── periods ───────────────────────────────────────────────────────────
    async def get_or_create_period(self, client_id: int, year: int, month: int) -> BillingPeriod:
        """Idempotent: returns the single (client, year, month) period."""
        existing = await self.billing.get_for_client_month(client_id, year, month)
        if existing is not None:
            return existing
        period = BillingPeriod(
            client_id=client_id,
            year=year,
            month=month,
            status=BillingStatus.DRAFT.value,
        )
        # The INSERT runs inside a SAVEPOINT so that losing the race rolls back
        # only the period, never the caller's pending unit of work (a DONE task,
        # a payment, ...). A bare session.rollback() would discard those changes.
        try:
            async with self.session.begin_nested():
                await self.billing.add(period)
                await self.session.flush()
            return period
        except IntegrityError:
            # Concurrent double-create: reuse the row the other worker committed.
            existing = await self.billing.get_for_client_month(client_id, year, month)
            if existing is None:  # pragma: no cover - defensive
                raise
            return existing

    async def get_period(self, period_id: int) -> BillingPeriod:
        period = await self.billing.get(period_id)
        if period is None:
            raise NotFoundError(f"BillingPeriod {period_id} not found")
        return period

    async def current_period(self, client_id: int, tz_name: str) -> BillingPeriod:
        year, month = current_month(tz_name)
        return await self.get_or_create_period(client_id, year, month)

    async def list_for_month(self, year: int, month: int) -> list[BillingPeriod]:
        return await self.billing.list_for_month(year, month)

    # ── reconciliation ────────────────────────────────────────────────────
    async def reconcile_draft(self, period: BillingPeriod) -> BillingPeriod:
        """Make draft invoice items match current DONE tasks + active charges.

        Idempotent: repeated calls never duplicate items. No-op for issued periods.
        """
        if period.status != BillingStatus.DRAFT.value:
            return period

        desired: dict[tuple[str, int], tuple[str, Decimal]] = {}

        for task in await self._done_tasks_for_period(period.id):
            desired[(InvoiceItemSource.TASK.value, task.id)] = (task.title, task.amount)

        for charge in await self._charges_for_period(period):
            desired[(InvoiceItemSource.RECURRING_CHARGE.value, charge.id)] = (
                charge.title,
                charge.amount,
            )

        items = await self.billing.list_items(period.id)
        by_source = {
            (item.source_type, item.source_id): item
            for item in items
            if item.source_id is not None
        }

        for key, (description, amount) in desired.items():
            item = by_source.get(key)
            if item is None:
                item = InvoiceItem(
                    billing_period_id=period.id,
                    source_type=key[0],
                    source_id=key[1],
                    description=description,
                    quantity=Decimal("1"),
                    unit_price=amount,
                    amount=amount,
                )
                await self.billing.add_item(item)
            elif item.description != description or item.amount != amount:
                item.description = description
                item.unit_price = amount
                item.amount = amount

        for (source_type, source_id), item in by_source.items():
            if source_type in (
                InvoiceItemSource.TASK.value,
                InvoiceItemSource.RECURRING_CHARGE.value,
            ) and (source_type, source_id) not in desired:
                await self.session.delete(item)

        await self.session.flush()
        return period

    async def _done_tasks_for_period(self, period_id: int) -> list[Task]:
        stmt = select(Task).where(
            Task.billing_period_id == period_id,
            Task.status == TaskStatus.DONE.value,
        )
        return list((await self.session.scalars(stmt)).all())

    async def _charges_for_period(self, period: BillingPeriod) -> list[RecurringCharge]:
        stmt = select(RecurringCharge).where(
            RecurringCharge.client_id == period.client_id,
            RecurringCharge.is_active.is_(True),
        )
        charges = list((await self.session.scalars(stmt)).all())
        return [c for c in charges if charge_active_in_month(c, period.year, period.month)]

    # ── manual items ──────────────────────────────────────────────────────
    async def add_manual_item(
        self, period_id: int, description: str, amount: Decimal
    ) -> InvoiceItem:
        period = await self.get_period(period_id)
        self._ensure_draft(period)
        item = InvoiceItem(
            billing_period_id=period_id,
            source_type=InvoiceItemSource.MANUAL.value,
            source_id=None,
            description=description,
            quantity=Decimal("1"),
            unit_price=amount,
            amount=amount,
        )
        await self.billing.add_item(item)
        return item

    # ── issue / snapshot ──────────────────────────────────────────────────
    async def issue(self, period: BillingPeriod) -> BillingPeriod:
        if period.status in (
            BillingStatus.ISSUED.value,
            BillingStatus.PARTIALLY_PAID.value,
            BillingStatus.PAID.value,
        ):
            return period  # idempotent
        if period.status != BillingStatus.DRAFT.value:
            raise InvalidTransitionError(f"Cannot issue period in {period.status}")
        await self.reconcile_draft(period)
        period.status = BillingStatus.ISSUED.value
        period.issued_at = now_utc()
        await self.session.flush()
        return period

    # ── totals ────────────────────────────────────────────────────────────
    async def totals(self, period: BillingPeriod) -> Totals:
        invoice = await self.billing.invoice_total(period.id)
        paid = await self.billing.paid_total(period.id)
        debt = max(Decimal("0"), invoice - paid)
        return Totals(invoice_total=invoice, paid_total=paid, debt=debt)

    async def items(self, period: BillingPeriod) -> list[InvoiceItem]:
        return await self.billing.list_items(period.id)

    async def payments(self, period: BillingPeriod) -> list:
        return await self.billing.list_payments(period.id)

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _ensure_draft(period: BillingPeriod) -> None:
        if period.status != BillingStatus.DRAFT.value:
            raise InvalidTransitionError(
                f"Financial data of period {period.id} is frozen (status={period.status})"
            )

    @staticmethod
    def period_key(dt: datetime, tz_name: str) -> tuple[int, int]:
        return local_month(dt, tz_name)

