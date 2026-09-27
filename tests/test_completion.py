from datetime import date
from decimal import Decimal

import pytest

from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.payment_service import PaymentService
from app.services.project_service import ProjectService
from app.services.recurring_service import RecurringService
from app.services.task_service import TaskService
from app.utils.money import to_decimal


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "10000000000000000", 1.2])
def test_invalid_money_rejected(value):
    with pytest.raises(ValueError):
        to_decimal(value)


async def test_project_cannot_belong_to_other_client(session):
    first = await ClientService(session).create("First")
    second = await ClientService(session).create("Second")
    project = await ProjectService(session).create(first.id, "Site")
    with pytest.raises(ValueError):
        await TaskService(session).create(second.id, "Wrong", project_id=project.id)


async def test_midmonth_service_is_billed_and_deactivation_reconciles(session):
    client = await ClientService(session).create("Client")
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)
    recurring = RecurringService(session)
    charge = await recurring.create(client.id, "Server", "5000", active_from=date(2026, 9, 15))
    assert (await billing.totals(period)).invoice_total == Decimal("5000")
    await recurring.deactivate(charge)
    assert (await billing.totals(period)).invoice_total == 0


async def test_dashboard_generates_recurring_only_month(session):
    client = await ClientService(session).create("Client")
    await RecurringService(session).create(
        client.id, "Server", "5000", active_from=date(2026, 1, 1)
    )
    stats = await DashboardService(session).month_stats(2026, 9)
    assert stats.accrued == Decimal("5000")


async def test_payment_request_is_idempotent(session):
    client = await ClientService(session).create("Client")
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)
    await billing.add_manual_item(period.id, "Work", Decimal("10000"))
    await billing.issue(period)
    svc = PaymentService(session)
    first = await svc.add_payment(period.id, "4000", idempotency_key="payment:123")
    second = await svc.add_payment(period.id, "4000", idempotency_key="payment:123")
    assert first.id == second.id
    assert (await billing.totals(period)).paid_total == Decimal("4000")


async def test_search_fields_case_insensitive_and_literal_wildcards(session):
    from app.services.search_service import SearchService

    client = await ClientService(session).create("Заказчик")
    await ClientService(session).add_field(client.id, "Email", "Owner@Example.test")
    project = await ProjectService(session).create(client.id, "Website")
    await ProjectService(session).add_field(project.id, "Server", "100%_READY")
    search = SearchService(session)
    assert [c.id for c in (await search.search("owner@example")).clients] == [client.id]
    assert [p.id for p in (await search.search("%_ready")).projects] == [project.id]
    assert (await search.search("   ")).clients == []


async def test_done_rejected_after_issue_keeps_task_new(session):
    from app.models.enums import TaskStatus
    from app.services.errors import InvoiceIssuedError
    from app.utils.time import current_month

    client = await ClientService(session).create("Client")
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, *current_month("UTC"))
    await billing.add_manual_item(period.id, "Work", Decimal("100"))
    await billing.issue(period)
    task = await TaskService(session).create(client.id, "Late work", amount="100")
    with pytest.raises(InvoiceIssuedError):
        await TaskService(session).set_status(task, TaskStatus.DONE)
    await session.flush()
    assert (task.status, task.completed_at, task.billing_period_id) == ("NEW", None, None)


async def test_source_survives_description_edit(session):
    from app.services.task_service import SourceData

    client = await ClientService(session).create("Client")
    svc = TaskService(session)
    task = await svc.create(client.id, "Work", source=SourceData(original_text="Original"))
    await svc.edit(task, description="Changed")
    assert (task.description, task.source.original_text) == ("Changed", "Original")
