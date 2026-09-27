"""Billing period and invoice snapshot tests."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.billing import BillingPeriod
from app.models.enums import BillingStatus, TaskStatus
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.errors import InvoiceIssuedError
from app.services.task_service import TaskService


async def _client(session):
    return await ClientService(session).create("Заказчик")


async def test_get_or_create_period_is_idempotent(session):
    client = await _client(session)
    billing = BillingService(session)
    p1 = await billing.get_or_create_period(client.id, 2026, 9)
    p2 = await billing.get_or_create_period(client.id, 2026, 9)
    assert p1.id == p2.id
    # Different month yields a different period.
    p3 = await billing.get_or_create_period(client.id, 2026, 10)
    assert p3.id != p1.id


async def test_period_unique_constraint(session):
    client = await _client(session)
    billing = BillingService(session)
    await billing.get_or_create_period(client.id, 2026, 9)
    dup = BillingPeriod(client_id=client.id, year=2026, month=9, status="DRAFT")
    session.add(dup)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_issue_snapshot_immutable(session):
    client = await _client(session)
    tasks = TaskService(session, tz_name="UTC")
    task = await tasks.create(client.id, "Работа", amount=Decimal("2000"))
    await tasks.set_status(task, TaskStatus.DONE)

    billing = BillingService(session)
    period = await billing.get_period(task.billing_period_id)
    await billing.issue(period)
    assert period.status == BillingStatus.ISSUED.value

    before = (await billing.totals(period)).invoice_total
    assert before == Decimal("2000")

    # Mutate the task directly (bypassing service) and re-reconcile.
    task.amount = Decimal("9999")
    await session.flush()
    await billing.reconcile_draft(period)

    after = (await billing.totals(period)).invoice_total
    assert after == before == Decimal("2000")


async def test_edit_amount_after_issue_raises(session):
    client = await _client(session)
    tasks = TaskService(session, tz_name="UTC")
    task = await tasks.create(client.id, "Работа", amount=Decimal("2000"))
    await tasks.set_status(task, TaskStatus.DONE)
    billing = BillingService(session)
    period = await billing.get_period(task.billing_period_id)
    await billing.issue(period)
    with pytest.raises(InvoiceIssuedError):
        await tasks.edit(task, amount=Decimal("3000"))


async def test_add_manual_item_and_totals(session):
    client = await _client(session)
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)
    await billing.add_manual_item(period.id, "Консультация", Decimal("1500"))
    await billing.add_manual_item(period.id, "Хостинг", Decimal("500"))
    totals = await billing.totals(period)
    assert totals.invoice_total == Decimal("2000")
    assert totals.paid_total == Decimal("0")
    assert totals.debt == Decimal("2000")
