"""Regression suite mapping the mandatory acceptance scenarios.

These tests are intentionally named after the business invariants from the
spec; some overlap with the unit tests but assert the end-to-end invariants.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import BillingStatus, TaskStatus
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.errors import AlreadyExistsError, InvoiceIssuedError, OverpaymentError
from app.services.payment_service import PaymentService
from app.services.recurring_service import RecurringService
from app.services.task_service import SourceData, TaskService
from app.utils.time import current_month


async def _client(session):
    return await ClientService(session).create("Заказчик")


async def test_duplicate_forward_creates_single_task(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    source = SourceData(telegram_chat_id=1, telegram_message_id=7, dedup_key="chat:1:msg:7")
    await svc.create_unique_from_source(client.id, "Работа", source=source)
    with pytest.raises(AlreadyExistsError):
        await svc.create_unique_from_source(client.id, "Работа", source=source)

    tasks = await svc.list_by_client(client.id)
    assert len(tasks) == 1


async def test_double_done_creates_one_billing_item(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Работа", amount=Decimal("1200"))
    await svc.set_status(task, TaskStatus.DONE)
    await svc.set_status(task, TaskStatus.DONE)

    billing = BillingService(session)
    period = await billing.get_period(task.billing_period_id)
    task_items = [i for i in await billing.items(period) if i.source_type == "TASK"]
    assert len(task_items) == 1


async def test_recurring_generation_idempotent_monthly(session):
    client = await _client(session)
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)

    recurring = RecurringService(session)
    await recurring.create(client.id, "Сервер", Decimal("8000"), active_from=date(2026, 1, 1))

    await billing.reconcile_draft(period)
    await billing.reconcile_draft(period)

    items = [i for i in await billing.items(period) if i.source_type == "RECURRING_CHARGE"]
    assert len(items) == 1


async def test_amount_change_after_issue_does_not_mutate_invoice(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Работа", amount=Decimal("2000"))
    await svc.set_status(task, TaskStatus.DONE)

    billing = BillingService(session)
    period = await billing.get_period(task.billing_period_id)
    await billing.issue(period)
    before = (await billing.totals(period)).invoice_total

    with pytest.raises(InvoiceIssuedError):
        await svc.edit(task, amount=Decimal("3000"))

    # Direct mutation + re-reconcile must be a no-op on an issued invoice.
    task.amount = Decimal("3000")
    await session.flush()
    await billing.reconcile_draft(period)
    assert (await billing.totals(period)).invoice_total == before


async def test_partial_then_full_payment(session):
    client = await _client(session)
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)
    await billing.add_manual_item(period.id, "Работа", Decimal("20000"))
    await billing.issue(period)

    payments = PaymentService(session)
    await payments.add_payment(period.id, Decimal("10000"))
    period = await billing.get_period(period.id)
    assert period.status == BillingStatus.PARTIALLY_PAID.value

    await payments.add_payment(period.id, Decimal("10000"))
    period = await billing.get_period(period.id)
    assert period.status == BillingStatus.PAID.value
    assert (await billing.totals(period)).debt == Decimal("0")


async def test_overpayment_is_rejected(session):
    client = await _client(session)
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)
    await billing.add_manual_item(period.id, "Работа", Decimal("10000"))
    await billing.issue(period)

    payments = PaymentService(session)
    with pytest.raises(OverpaymentError):
        await payments.add_payment(period.id, Decimal("10001"))


async def test_charge_not_generated_after_end_date(session):
    client = await _client(session)
    billing = BillingService(session)
    recurring = RecurringService(session)
    await recurring.create(
        client.id,
        "Подписка",
        Decimal("5000"),
        active_from=date(2026, 1, 1),
        active_until=date(2026, 8, 31),
    )

    period = await billing.get_or_create_period(client.id, 2026, 9)
    await billing.reconcile_draft(period)
    items = [i for i in await billing.items(period) if i.source_type == "RECURRING_CHARGE"]
    assert items == []


async def test_manual_doz_without_forward_is_not_deduped(session):
    """A дозаказ started from the client card has no forwarded message.

    Regression: the handler used to synthesise "chat:None:msg:None" for every
    manual дозаказ, so the UNIQUE dedup_key let the first one through and
    rejected every later дозаказ for every client.
    """
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")

    first = SourceData.from_message(chat_id=None, message_id=None, original_text=None)
    second = SourceData.from_message(chat_id=None, message_id=None, original_text=None)
    assert first.dedup_key is None and second.dedup_key is None

    await svc.create_unique_from_source(client.id, "Первый", amount=Decimal("100"), source=first)
    await svc.create_unique_from_source(client.id, "Второй", amount=Decimal("200"), source=second)

    tasks = await svc.list_by_client(client.id)
    assert len(tasks) == 2


async def test_forwarded_message_still_deduped(session):
    """The dedup guard must keep working for real forwarded messages."""
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")

    source = SourceData.from_message(chat_id=10, message_id=20, original_text="Текст")
    assert source.dedup_key == "chat:10:msg:20"

    await svc.create_unique_from_source(client.id, "Работа", source=source)
    with pytest.raises(AlreadyExistsError):
        await svc.create_unique_from_source(
            client.id,
            "Работа",
            source=SourceData.from_message(chat_id=10, message_id=20),
        )


async def test_done_after_issued_invoice_keeps_revenue_in_next_draft(session):
    """Completing work for a month whose invoice is already issued must not
    silently drop the revenue (AGENTS.md §17/§19)."""
    client = await _client(session)
    # Completing a task stamps completed_at with "now", so target the current
    # month's period to keep the scenario deterministic on any run date.
    year, month = current_month("UTC")

    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, year, month)
    await billing.add_manual_item(period.id, "Работа", Decimal("10000"))
    await billing.issue(period)

    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Поздняя работа", amount=Decimal("5000"))

    await svc.set_status(task, TaskStatus.DONE)
    new_draft = await billing.get_period(task.billing_period_id)
    assert new_draft.id != period.id
    assert (await billing.totals(new_draft)).invoice_total == Decimal("5000")

    # The issued snapshot must be untouched.
    assert (await billing.totals(period)).invoice_total == Decimal("10000")


async def test_empty_month_yields_zero_stats(session):
    client = await _client(session)
    dash = DashboardService(session, tz_name="UTC")
    stats = await dash.client_month_stats(client.id, 2026, 12)
    assert stats.accrued == Decimal("0")
    assert stats.issued == Decimal("0")
    assert stats.paid == Decimal("0")
    assert stats.debt == Decimal("0")
