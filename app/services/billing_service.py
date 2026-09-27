"""Billing service: periods, idempotent reconciliation, invoice snapshot."""

from __future__ import annotations

from calendar import monthrange
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
from app.services.errors import InvalidAmountError, InvalidTransitionError, NotFoundError
from app.services.validation import lock_client
from app.utils.money import to_decimal
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
    if date(year, month, monthrange(year, month)[1]) < charge.active_from:
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
        """Return the open draft; issued invoices never block another invoice."""
        date(year, month, 1)
        await lock_client(self.session, client_id)
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

    async def lock_period(self, period_id: int) -> BillingPeriod:
        period = await self.get_period(period_id)
        await lock_client(self.session, period.client_id)
        await self.session.refresh(
            period, attribute_names=["status", "issued_at", "closed_at"], with_for_update=True
        )
        return period

    async def generate_month(self, year: int, month: int, client_id: int | None = None) -> None:
        date(year, month, 1)
        stmt = select(RecurringCharge).order_by(RecurringCharge.client_id)
        if client_id is not None:
            stmt = stmt.where(RecurringCharge.client_id == client_id)
        charges = list(
            (await self.session.scalars(stmt.execution_options(populate_existing=True))).all()
        )
        clients = sorted({c.client_id for c in charges if charge_active_in_month(c, year, month)})
        for cid in clients:
            await lock_client(self.session, cid)
            period = await self.billing.get_for_client_month(cid, year, month)
            billed = await self._billed_recurring_ids(
                cid, year, month, period.id if period else None
            )
            pending = any(
                c.client_id == cid and c.id not in billed and charge_active_in_month(c, year, month)
                for c in charges
            )
            if period is None and not pending:
                continue
            period = period or await self.get_or_create_period(cid, year, month)
            await self.reconcile_draft(period)

    async def current_period(self, client_id: int, tz_name: str) -> BillingPeriod:
        year, month = current_month(tz_name)
        return await self.get_or_create_period(client_id, year, month)

    async def list_for_month(self, year: int, month: int) -> list[BillingPeriod]:
        return await self.billing.list_for_month(year, month)

    async def list_for_client(self, client_id: int) -> list[BillingPeriod]:
        return await self.billing.list_for_client(client_id)

    async def _billed_recurring_ids(
        self, client_id: int, year: int, month: int, exclude_id: int | None = None
    ) -> set[int]:
        # The client lock serializes issue/generation across all that client's invoices.
        # SUPERSEDED sources still prove that their recurring charge was allocated;
        # their successors retain frozen TRANSFER copies, possibly in another month.
        stmt = (
            select(InvoiceItem.source_id)
            .join(BillingPeriod)
            .where(
                BillingPeriod.client_id == client_id,
                BillingPeriod.year == year,
                BillingPeriod.month == month,
                BillingPeriod.status != BillingStatus.CANCELLED.value,
                InvoiceItem.source_type == InvoiceItemSource.RECURRING_CHARGE.value,
                InvoiceItem.source_id.is_not(None),
            )
        )
        if exclude_id is not None:
            stmt = stmt.where(BillingPeriod.id != exclude_id)
        return set((await self.session.scalars(stmt)).all())

    # ── reconciliation ────────────────────────────────────────────────────
    async def reconcile_draft(self, period: BillingPeriod) -> BillingPeriod:
        """Make draft invoice items match current DONE tasks + active charges.

        Idempotent: repeated calls never duplicate items. No-op for issued periods.
        """
        period = await self.lock_period(period.id)
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
            (item.source_type, item.source_id): item for item in items if item.source_id is not None
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
            if (
                source_type
                in (
                    InvoiceItemSource.TASK.value,
                    InvoiceItemSource.RECURRING_CHARGE.value,
                )
                and (source_type, source_id) not in desired
            ):
                await self.session.delete(item)

        await self.session.flush()
        return period

    async def _done_tasks_for_period(self, period_id: int) -> list[Task]:
        stmt = select(Task).where(
            Task.billing_period_id == period_id,
            Task.status == TaskStatus.DONE.value,
        )
        return list(
            (await self.session.scalars(stmt.execution_options(populate_existing=True))).all()
        )

    async def _charges_for_period(self, period: BillingPeriod) -> list[RecurringCharge]:
        stmt = select(RecurringCharge).where(
            RecurringCharge.client_id == period.client_id,
            RecurringCharge.is_active.is_(True),
        )
        charges = list(
            (await self.session.scalars(stmt.execution_options(populate_existing=True))).all()
        )
        billed = await self._billed_recurring_ids(
            period.client_id, period.year, period.month, period.id
        )
        return [
            c
            for c in charges
            if c.id not in billed and charge_active_in_month(c, period.year, period.month)
        ]

    # ── manual items ──────────────────────────────────────────────────────
    async def add_manual_item(
        self, period_id: int, description: str, amount: Decimal
    ) -> InvoiceItem:
        period = await self.lock_period(period_id)
        self._ensure_draft(period)
        amount = to_decimal(amount)
        if not description.strip():
            raise ValueError("Введите описание позиции")
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
        period = await self.lock_period(period.id)
        if period.status in (
            BillingStatus.ISSUED.value,
            BillingStatus.PARTIALLY_PAID.value,
            BillingStatus.PAID.value,
        ):
            return period  # idempotent
        if period.status != BillingStatus.DRAFT.value:
            raise InvalidTransitionError(f"Cannot issue period in {period.status}")
        await self.reconcile_draft(period)
        if (await self.totals(period)).invoice_total <= 0:
            raise InvalidAmountError(
                "Нельзя выставить счёт на 0 ₽. Сначала отметьте задачу «Выполнено» или добавьте услугу."
            )
        period.status = BillingStatus.ISSUED.value
        period.issued_at = now_utc()
        await self.session.flush()
        return period

    async def ensure_payable(self, period: BillingPeriod) -> Totals:
        totals = await self.totals(period)
        if totals.debt <= 0:
            raise ValueError("Долга нет — оплата по этому счёту не требуется.")
        if period.status not in (BillingStatus.ISSUED.value, BillingStatus.PARTIALLY_PAID.value):
            raise ValueError("Сначала выставьте счёт, затем внесите оплату.")
        return totals

    # ── totals ────────────────────────────────────────────────────────────
    async def totals(self, period: BillingPeriod) -> Totals:
        invoice = await self.billing.invoice_total(period.id)
        paid = await self.billing.paid_total(period.id)
        debt = (
            Decimal("0")
            if period.status == BillingStatus.SUPERSEDED.value
            else max(Decimal("0"), invoice - paid)
        )
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
