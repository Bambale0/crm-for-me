from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import TaskStatus
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.errors import InvalidAmountError
from app.services.payment_service import PaymentService
from app.services.recurring_service import RecurringService
from app.services.task_service import TaskService
from app.utils.time import current_month


async def test_two_invoices_same_month_keep_independent_totals_and_payments(session):
    client = await ClientService(session).create("Client")
    billing, tasks = BillingService(session), TaskService(session)
    first_task = await tasks.create(client.id, "First", amount="19500")
    await tasks.set_status(first_task, TaskStatus.DONE)
    first = await billing.get_period(first_task.billing_period_id)
    await billing.issue(first)
    second_task = await tasks.create(client.id, "Second", amount="5000")
    await tasks.set_status(second_task, TaskStatus.DONE)
    second = await billing.get_period(second_task.billing_period_id)
    await billing.issue(second)
    await PaymentService(session).add_payment(first.id, "19500", idempotency_key="first-payment")
    assert first.id != second.id
    assert (await billing.totals(first)).debt == 0
    assert (await billing.totals(second)).debt == Decimal("5000")
    stats = await DashboardService(session).client_month_stats(client.id, *current_month("UTC"))
    assert (stats.accrued, stats.issued, stats.paid, stats.debt) == (
        Decimal("24500"),
        Decimal("24500"),
        Decimal("19500"),
        Decimal("5000"),
    )


async def test_recurring_charge_is_not_billed_again_in_next_invoice_same_month(session):
    client = await ClientService(session).create("Client")
    billing = BillingService(session)
    await RecurringService(session).create(
        client.id, "Server", "5000", active_from=date(2026, 1, 1)
    )
    first = await billing.get_or_create_period(client.id, 2026, 9)
    await billing.issue(first)
    second = await billing.get_or_create_period(client.id, 2026, 9)
    assert first.id != second.id
    await billing.add_manual_item(second.id, "Extra work", Decimal("1000"))
    await billing.reconcile_draft(second)
    assert (await billing.totals(second)).invoice_total == Decimal("1000")
    await billing.issue(second)
    await billing.generate_month(2026, 9)
    assert len(await billing.list_for_month(2026, 9)) == 2
    october = await billing.get_or_create_period(client.id, 2026, 10)
    await billing.reconcile_draft(october)
    assert (await billing.totals(october)).invoice_total == Decimal("5000")


async def test_empty_or_zero_invoice_cannot_be_issued(session):
    client = await ClientService(session).create("Client")
    billing = BillingService(session)
    invoice = await billing.get_or_create_period(client.id, 2026, 9)
    with pytest.raises(InvalidAmountError):
        await billing.issue(invoice)
    assert invoice.status == "DRAFT"
    await billing.add_manual_item(invoice.id, "Free work", Decimal("0"))
    with pytest.raises(InvalidAmountError):
        await billing.issue(invoice)
