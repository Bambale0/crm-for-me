"""Payment tests: partial/full payment, debt and overpayment guard."""

from decimal import Decimal

import pytest

from app.models.enums import BillingStatus
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.errors import InvalidTransitionError, OverpaymentError
from app.services.payment_service import PaymentService


async def _issued_period(session, client_id):
    billing = BillingService(session)
    period = await billing.get_or_create_period(client_id, 2026, 9)
    await billing.add_manual_item(period.id, "Работа", Decimal("20000"))
    await billing.issue(period)
    return period


async def test_partial_payment(session):
    client = await ClientService(session).create("Заказчик")
    period = await _issued_period(session, client.id)

    payments = PaymentService(session)
    await payments.add_payment(period.id, Decimal("10000"))

    billing = BillingService(session)
    period = await billing.get_period(period.id)
    assert period.status == BillingStatus.PARTIALLY_PAID.value
    totals = await billing.totals(period)
    assert totals.paid_total == Decimal("10000")
    assert totals.debt == Decimal("10000")


async def test_full_payment(session):
    client = await ClientService(session).create("Заказчик")
    period = await _issued_period(session, client.id)

    payments = PaymentService(session)
    await payments.add_payment(period.id, Decimal("20000"))

    billing = BillingService(session)
    period = await billing.get_period(period.id)
    assert period.status == BillingStatus.PAID.value
    totals = await billing.totals(period)
    assert totals.paid_total == Decimal("20000")
    assert totals.debt == Decimal("0")


async def test_overpayment_rejected(session):
    client = await ClientService(session).create("Заказчик")
    period = await _issued_period(session, client.id)

    payments = PaymentService(session)
    with pytest.raises(OverpaymentError):
        await payments.add_payment(period.id, Decimal("20001"))


async def test_cannot_pay_draft(session):
    client = await ClientService(session).create("Заказчик")
    billing = BillingService(session)
    period = await billing.get_or_create_period(client.id, 2026, 9)
    await billing.add_manual_item(period.id, "Работа", Decimal("1000"))

    payments = PaymentService(session)
    with pytest.raises(InvalidTransitionError):
        await payments.add_payment(period.id, Decimal("1000"))
