from decimal import Decimal

import pytest

from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.payment_service import PaymentService


async def seed(session):
    billing = BillingService(session)
    cid = (await ClientService(session).create("Client")).id
    source = await billing.get_or_create_period(cid, 2026, 9)
    first = await billing.add_manual_item(source.id, "Move", Decimal("19500"))
    second = await billing.add_manual_item(source.id, "Keep", Decimal("5000"))
    await billing.issue(source)
    target = await billing.get_or_create_period(cid, 2026, 9)
    await billing.add_manual_item(target.id, "Target", Decimal("1000"))
    await billing.issue(target)
    return billing, cid, source, target, first, second


async def test_transfer_selected_item_keeps_history_remainder_and_total_debt(session):
    from app.services.invoice_transfer_service import InvoiceTransferService

    billing, cid, source, target, first, second = await seed(session)
    transfer = InvoiceTransferService(session)
    preview = await transfer.preview(target.id, [first.id])
    result = await transfer.transfer(
        target.id, [first.id], key="action", expected=preview.fingerprint
    )
    assert (source.status, target.status, result.status) == ("SUPERSEDED", "SUPERSEDED", "ISSUED")
    assert (await billing.totals(result)).invoice_total == Decimal("20500")
    assert (await billing.totals(source)).invoice_total == Decimal("24500")
    assert (await billing.totals(source)).debt == 0
    assert len(await billing.items(source)) == 2
    replacements = await transfer.replacements(source.id)
    remaining = next(p for p in replacements if p.id != result.id)
    assert (await billing.totals(remaining)).invoice_total == Decimal("5000")
    assert (await billing.items(remaining))[0].origin_item_id == second.id
    assert await DashboardService(session).total_debt(cid) == Decimal("25500")
    stats = await DashboardService(session).client_month_stats(cid, 2026, 9)
    assert stats.accrued == stats.issued == stats.debt == Decimal("25500")
    replay = await transfer.transfer(
        target.id, [first.id], key="action", expected=preview.fingerprint
    )
    assert replay.id == result.id
    await PaymentService(session).add_payment(result.id, "20500")
    assert await DashboardService(session).total_debt(cid) == Decimal("5000")


async def test_transfer_refuses_paid_source_and_foreign_client(session):
    from app.services.invoice_transfer_service import InvoiceTransferService

    billing, _, source, target, first, _ = await seed(session)
    svc = InvoiceTransferService(session)
    await PaymentService(session).add_payment(source.id, "100")
    with pytest.raises(ValueError, match="оплат"):
        await svc.preview(target.id, [first.id])
    other = (await ClientService(session).create("Other")).id
    invoice = await billing.get_or_create_period(other, 2026, 9)
    item = await billing.add_manual_item(invoice.id, "Other", Decimal("100"))
    with pytest.raises(ValueError, match="клиент"):
        await svc.preview(target.id, [item.id])
    assert source.status == "PARTIALLY_PAID" and target.status == "ISSUED"


async def test_transfer_rejects_changed_preview_and_reused_key(session):
    from app.services.invoice_transfer_service import InvoiceTransferService

    billing, _, _, target, first, second = await seed(session)
    svc = InvoiceTransferService(session)
    with pytest.raises(ValueError, match="изменились"):
        await svc.transfer(target.id, [first.id], key="action", expected="outdated")
    preview = await svc.preview(target.id, [first.id])
    await svc.transfer(target.id, [first.id], key="action", expected=preview.fingerprint)
    with pytest.raises(ValueError, match="другого"):
        await svc.transfer(target.id, [second.id], key="action", expected=preview.fingerprint)


async def test_transfer_drafts_across_months_keeps_recurring_charge_once(session):
    from datetime import date

    from app.services.invoice_transfer_service import InvoiceTransferService
    from app.services.recurring_service import RecurringService

    billing = BillingService(session)
    cid = (await ClientService(session).create("Client")).id
    recurring = RecurringService(session)
    charge = await recurring.create(cid, "Server", "5000", active_from=date(2026, 1, 1))
    source = await billing.get_or_create_period(cid, 2026, 8)
    await billing.reconcile_draft(source)
    target = await billing.get_or_create_period(cid, 2026, 9)
    await billing.reconcile_draft(target)
    svc = InvoiceTransferService(session)
    selected = [(await billing.items(source))[0].id]
    preview = await svc.preview(target.id, selected)
    result = await svc.transfer(target.id, selected, key="months", expected=preview.fingerprint)
    await recurring.update_amount(charge, "9000")
    await billing.generate_month(2026, 8)
    await billing.generate_month(2026, 9)
    await billing.reconcile_draft(result)
    assert (await billing.totals(result)).invoice_total == Decimal("10000")
    assert len(await billing.list_for_month(2026, 8)) == 1
    assert len(await billing.items(result)) == 2
    await billing.issue(result)
    assert await DashboardService(session).total_debt(cid) == Decimal("10000")


async def test_transfer_keeps_draft_remainder_and_allows_new_work(session):
    from app.models.enums import TaskStatus
    from app.services.invoice_transfer_service import InvoiceTransferService
    from app.services.task_service import TaskService
    from app.utils.time import current_month

    billing = BillingService(session)
    cid = (await ClientService(session).create("Client")).id
    target = await billing.get_or_create_period(cid, 2026, 1)
    await billing.add_manual_item(target.id, "Target", Decimal("1000"))
    tasks = TaskService(session)
    first = await tasks.create(cid, "Move", amount="2000")
    await tasks.set_status(first, TaskStatus.DONE)
    second = await tasks.create(cid, "Keep", amount="3000")
    await tasks.set_status(second, TaskStatus.DONE)
    source = await billing.get_period(first.billing_period_id)
    ids = [i.id for i in await billing.items(source) if i.source_id == first.id]
    svc = InvoiceTransferService(session)
    preview = await svc.preview(target.id, ids)
    result = await svc.transfer(target.id, ids, key="drafts", expected=preview.fingerprint)
    third = await tasks.create(cid, "New", amount="4000")
    await tasks.set_status(third, TaskStatus.DONE)
    remaining = await billing.get_or_create_period(cid, *current_month("UTC"))
    assert remaining.id == third.billing_period_id != source.id
    assert (await billing.totals(remaining)).invoice_total == Decimal("7000")
    assert (await billing.totals(result)).invoice_total == Decimal("3000")


async def test_transfer_again_retains_chain_of_original_invoices(session):
    from app.services.invoice_transfer_service import InvoiceTransferService

    billing, cid, source, target, first, _ = await seed(session)
    svc = InvoiceTransferService(session)
    preview = await svc.preview(target.id, [first.id])
    combined = await svc.transfer(target.id, [first.id], key="one", expected=preview.fingerprint)
    third = await billing.get_or_create_period(cid, 2026, 9)
    selected = [i.id for i in await billing.items(combined)]
    preview = await svc.preview(third.id, selected)
    final = await svc.transfer(third.id, selected, key="two", expected=preview.fingerprint)
    assert (await svc.replacements(third.id))[0].id == final.id
    assert combined.status == "SUPERSEDED"
    assert (await svc.replacements(target.id))[0].id == combined.id
    assert (await svc.replacements(combined.id))[0].id == final.id
    assert len(await billing.items(final)) == 2
    await billing.issue(final)
    assert await DashboardService(session).total_debt(cid) == Decimal("25500")
