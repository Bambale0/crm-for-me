"""Dashboard / statistics service."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingPeriod
from app.models.enums import BillingStatus, TaskStatus
from app.models.task import Task
from app.repositories.billing import BillingRepository
from app.services.billing_service import BillingService
from app.utils.time import local_month, now_utc

ISSUED_STATUSES = (
    BillingStatus.ISSUED.value,
    BillingStatus.PARTIALLY_PAID.value,
    BillingStatus.PAID.value,
)


@dataclass(frozen=True)
class MonthStats:
    accrued: Decimal
    issued: Decimal
    paid: Decimal
    debt: Decimal


class DashboardService:
    def __init__(self, session: AsyncSession, tz_name: str = "UTC") -> None:
        self.session = session
        self.tz = tz_name
        self.billing = BillingService(session)
        self.billing_repo = BillingRepository(session)

    async def task_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for status in (TaskStatus.NEW, TaskStatus.IN_PROGRESS):
            stmt = select(func.count()).select_from(Task).where(Task.status == status.value)
            counts[status.value] = int(await self.session.scalar(stmt) or 0)
        counts["DONE_THIS_MONTH"] = await self._done_this_month_count()
        return counts

    async def _done_this_month_count(self) -> int:
        year, month = local_month(now_utc(), self.tz)
        tasks = await self._tasks_completed_in(year, month)
        return len(tasks)

    async def _tasks_completed_in(self, year: int, month: int) -> list[Task]:
        stmt = select(Task).where(Task.status == TaskStatus.DONE.value)
        tasks = list((await self.session.scalars(stmt)).all())
        return [
            t
            for t in tasks
            if t.completed_at is not None and local_month(t.completed_at, self.tz) == (year, month)
        ]

    async def month_stats(self, year: int, month: int) -> MonthStats:
        await self.billing.generate_month(year, month)
        periods = await self.billing_repo.list_for_month(year, month)
        accrued = Decimal("0")
        issued = Decimal("0")
        paid = Decimal("0")
        debt = Decimal("0")

        for period in periods:
            if period.status in (BillingStatus.CANCELLED.value, BillingStatus.SUPERSEDED.value):
                continue
            if period.status == BillingStatus.DRAFT.value:
                await self.billing.reconcile_draft(period)
            invoice = await self.billing_repo.invoice_total(period.id)
            period_paid = await self.billing_repo.paid_total(period.id)
            accrued += invoice
            if period.status in ISSUED_STATUSES:
                issued += invoice
                paid += period_paid
                debt += max(Decimal("0"), invoice - period_paid)

        return MonthStats(accrued=accrued, issued=issued, paid=paid, debt=debt)

    async def client_month_stats(self, client_id: int, year: int, month: int) -> MonthStats:
        await self.billing.generate_month(year, month, client_id)
        periods = [
            p
            for p in await self.billing_repo.list_for_month(year, month)
            if p.client_id == client_id
        ]
        accrued = issued = paid = debt = Decimal("0")
        for period in periods:
            if period.status in (BillingStatus.CANCELLED.value, BillingStatus.SUPERSEDED.value):
                continue
            await self.billing.reconcile_draft(period)
            totals = await self.billing.totals(period)
            accrued += totals.invoice_total
            if period.status in ISSUED_STATUSES:
                issued += totals.invoice_total
                paid += totals.paid_total
                debt += totals.debt
        return MonthStats(accrued=accrued, issued=issued, paid=paid, debt=debt)

    async def total_debt(self, client_id: int) -> Decimal:
        stmt = (
            select(BillingPeriod)
            .where(BillingPeriod.client_id == client_id)
            .where(BillingPeriod.status.in_(ISSUED_STATUSES))
        )
        periods = list((await self.session.scalars(stmt)).all())
        debt = Decimal("0")
        for period in periods:
            invoice = await self.billing_repo.invoice_total(period.id)
            paid = await self.billing_repo.paid_total(period.id)
            debt += max(Decimal("0"), invoice - paid)
        return debt

    async def active_task_count(self, client_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(
                Task.client_id == client_id,
                Task.status.in_((TaskStatus.NEW.value, TaskStatus.IN_PROGRESS.value)),
            )
        )
        return int(await self.session.scalar(stmt) or 0)
