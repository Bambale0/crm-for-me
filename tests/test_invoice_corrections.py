from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.billing import InvoiceItem, InvoiceItemCorrection, Payment
from app.models.enums import TaskStatus
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.errors import OverpaymentError
from app.services.invoice_transfer_service import InvoiceTransferService
from app.services.payment_service import PaymentService
from app.services.task_service import TaskService


async def seed(session):
    cid = (await ClientService(session).create("Client")).id
    billing = BillingService(session)
    invoice = await billing.get_or_create_period(cid, 2026, 9)
    item = await billing.add_manual_item(invoice.id, "70000", Decimal("70000"))
    return billing, invoice, item


async def test_correct_paid_invoice_preserves_snapshot_and_payment(session):
    billing, invoice, item = await seed(session)
    await PaymentService(session).add_payment(invoice.id, "20000", issue_draft=True)
    revision = await billing.correct_item(
        item.id, description="Разработка", amount="65000", key="correct-1", expected_revision=0
    )
    assert item.amount == 70000 and item.description == "70000"
    assert revision.previous_amount == 70000 and revision.previous_description == "70000"
    assert (await billing.item(item.id)).description == "Разработка"
    totals = await billing.totals(invoice)
    assert (totals.invoice_total, totals.paid_total, totals.debt) == (65000, 20000, 45000)
    assert (await DashboardService(session).overall_stats()).debt == 45000
    assert await session.scalar(select(func.count()).select_from(Payment)) == 1
    again = await billing.correct_item(
        item.id, description="Разработка", amount="65000", key="correct-1", expected_revision=0
    )
    assert again.id == revision.id
    with pytest.raises(ValueError, match="меньше уже полученной"):
        await billing.correct_item(
            item.id,
            description="Слишком мало",
            amount="19999",
            key="invalid",
            expected_revision=revision.id,
        )
    assert (await billing.totals(invoice)).debt == 45000
    assert await session.scalar(select(func.count()).select_from(InvoiceItemCorrection)) == 1
    paid = await billing.correct_item(
        item.id,
        description="Разработка",
        amount="20000",
        key="settle",
        expected_revision=revision.id,
    )
    assert invoice.status == "PAID"
    await billing.correct_item(
        item.id, description="Дополнено", amount="30000", key="reopen", expected_revision=paid.id
    )
    assert invoice.status == "PARTIALLY_PAID" and invoice.closed_at is None
    assert (await billing.totals(invoice)).debt == 10000


async def test_corrected_task_draft_survives_reconciliation_and_source_edits(session):
    cid = (await ClientService(session).create("Client")).id
    tasks, billing = TaskService(session), BillingService(session)
    task = await tasks.create(cid, "Original task", amount="70000")
    await tasks.set_status(task, TaskStatus.DONE)
    invoice = await billing.get_period(task.billing_period_id)
    item = (await billing.items(invoice))[0]
    await billing.correct_item(
        item.id, description="Corrected", amount="60000", key="draft", expected_revision=0
    )
    await tasks.edit(task, title="Source changed", amount="80000")
    await billing.reconcile_draft(invoice)
    assert (await billing.items(invoice))[0].description == "Corrected"
    assert (await billing.totals(invoice)).invoice_total == 60000
    original = await session.get(InvoiceItem, item.id)
    assert original.description == "Original task" and original.amount == 70000


async def test_transfer_uses_corrected_amount_and_title_once(session):
    billing, source, item = await seed(session)
    await billing.issue(source)
    await billing.correct_item(
        item.id, description="Corrected", amount="65000", key="edit", expected_revision=0
    )
    target = await billing.get_or_create_period(source.client_id, 2026, 9)
    await billing.add_manual_item(target.id, "Other", Decimal("10000"))
    transfer = InvoiceTransferService(session)
    preview = await transfer.preview(target.id, [item.id])
    assert preview.total == 75000
    combined = await transfer.transfer(
        target.id, [item.id], key="transfer", expected=preview.fingerprint
    )
    assert (await billing.totals(combined)).invoice_total == 75000
    assert (await DashboardService(session).overall_stats()).debt == 75000
    with pytest.raises(ValueError, match="нельзя корректировать"):
        await billing.correct_item(
            item.id, description="Stale", amount="80000", key="stale", expected_revision=1
        )


async def test_payment_draft_rejects_overpayment_without_issuing_and_dedups(session):
    billing, invoice, _ = await seed(session)
    payments = PaymentService(session)
    with pytest.raises(OverpaymentError, match="exceeds remaining debt"):
        await payments.add_payment(invoice.id, "80000", issue_draft=True)
    assert invoice.status == "DRAFT"
    first = await payments.add_payment(
        invoice.id, "20000", issue_draft=True, idempotency_key="first"
    )
    again = await payments.add_payment(
        invoice.id, "20000", issue_draft=True, idempotency_key="first"
    )
    assert first.id == again.id
    assert (await billing.totals(invoice)).debt == 50000
