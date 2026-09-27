"""Recurring charge generation tests."""

from datetime import date
from decimal import Decimal

from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.recurring_service import RecurringService


async def _client(session):
    return await ClientService(session).create("Заказчик")


async def test_recurring_generation_idempotent(session):
    client = await _client(session)
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)

    recurring = RecurringService(session)
    await recurring.create(
        client.id, "Сервер", Decimal("8000"), active_from=date(2026, 1, 1)
    )

    await billing.reconcile_draft(period)
    await billing.reconcile_draft(period)  # must not duplicate

    items = await billing.items(period)
    recurring_items = [i for i in items if i.source_type == "RECURRING_CHARGE"]
    assert len(recurring_items) == 1
    assert recurring_items[0].amount == Decimal("8000")


async def test_charge_not_active_before_start(session):
    client = await _client(session)
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 1)

    recurring = RecurringService(session)
    await recurring.create(
        client.id, "Сервер", Decimal("8000"), active_from=date(2026, 6, 1)
    )
    await billing.reconcile_draft(period)
    items = await billing.items(period)
    assert [i for i in items if i.source_type == "RECURRING_CHARGE"] == []


async def test_charge_stops_after_active_until(session):
    client = await _client(session)
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)

    recurring = RecurringService(session)
    await recurring.create(
        client.id,
        "Сервер",
        Decimal("8000"),
        active_from=date(2026, 1, 1),
        active_until=date(2026, 8, 31),
    )
    await billing.reconcile_draft(period)
    items = await billing.items(period)
    assert [i for i in items if i.source_type == "RECURRING_CHARGE"] == []
