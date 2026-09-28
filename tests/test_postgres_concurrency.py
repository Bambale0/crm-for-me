"""Real PostgreSQL races on independent committed connections."""

import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.billing import Payment
from app.models.enums import TaskStatus
from app.models.task import Task
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.errors import AlreadyExistsError, DomainError, OverpaymentError
from app.services.payment_service import PaymentService
from app.services.task_service import SourceData, TaskService

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="Requires migrated PostgreSQL TEST_DATABASE_URL"
)


async def seed(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        client = await ClientService(session).create("Concurrent client")
        svc = BillingService(session)
        period = await svc.get_or_create_period(client.id, 2026, 9)
        await svc.add_manual_item(period.id, "Work", Decimal("10000"))
        await svc.issue(period)
        return factory, client.id, period.id


async def test_concurrent_payments_cannot_overpay(engine):
    factory, _, pid = await seed(engine)
    barrier = asyncio.Barrier(2)

    async def pay(key):
        try:
            async with factory.begin() as session:
                # Both sessions hold the same stale initial state before locking.
                await BillingService(session).get_period(pid)
                await barrier.wait()
                await PaymentService(session).add_payment(pid, "6000", idempotency_key=key)
            return "ok"
        except OverpaymentError:
            return "rejected"

    results = await asyncio.wait_for(asyncio.gather(pay("a"), pay("b")), 10)
    async with factory() as session:
        total = await session.scalar(select(func.sum(Payment.amount)))
    assert (sorted(results), total) == (["ok", "rejected"], Decimal("6000"))


async def test_concurrent_replayed_payment_books_once(engine):
    factory, _, pid = await seed(engine)
    barrier = asyncio.Barrier(2)

    async def pay():
        async with factory.begin() as session:
            await barrier.wait()
            return (
                await PaymentService(session).add_payment(
                    pid, "10000", idempotency_key="same-action"
                )
            ).id

    ids = await asyncio.wait_for(asyncio.gather(pay(), pay()), 10)
    async with factory() as session:
        period = await BillingService(session).get_period(pid)
        count = await session.scalar(select(func.count()).select_from(Payment))
    assert (ids[0] == ids[1], count, period.status) == (True, 1, "PAID")


async def test_concurrent_done_and_reconciliation_one_item(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        client = await ClientService(session).create("Client")
        task = await TaskService(session).create(client.id, "Task", amount="5000")
        tid = task.id
    barrier = asyncio.Barrier(2)

    async def done():
        async with factory.begin() as session:
            svc = TaskService(session)
            task = await svc.get(tid)
            await barrier.wait()
            await svc.set_status(task, TaskStatus.DONE)
            return task.billing_period_id

    periods = await asyncio.wait_for(asyncio.gather(done(), done()), 10)
    async with factory() as session:
        svc = BillingService(session)
        period = await svc.get_period(periods[0])
        items = await svc.items(period)
    assert (periods[0] == periods[1], len(items), items[0].amount) == (True, 1, Decimal("5000"))


async def test_concurrent_same_source_cannot_create_orphan_task(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
    barrier = asyncio.Barrier(2)

    async def create():
        try:
            async with factory.begin() as session:
                await barrier.wait()
                await TaskService(session).create_unique_from_source(
                    cid, "Forward", source=SourceData(dedup_key="same")
                )
            return "ok"
        except AlreadyExistsError:
            return "duplicate"

    results = await asyncio.wait_for(asyncio.gather(create(), create()), 10)
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(Task))
    assert (sorted(results), count) == (["duplicate", "ok"], 1)


async def test_issue_and_task_edit_keep_snapshot_consistent(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
        svc = TaskService(session)
        task = await svc.create(cid, "Work", amount="5000")
        await svc.set_status(task, TaskStatus.DONE)
        tid, pid = task.id, task.billing_period_id
    barrier = asyncio.Barrier(2)

    async def issue():
        async with factory.begin() as session:
            svc = BillingService(session)
            period = await svc.get_period(pid)
            await barrier.wait()
            await svc.issue(period)

    async def edit():
        try:
            async with factory.begin() as session:
                svc = TaskService(session)
                task = await svc.get(tid)
                await barrier.wait()
                await svc.edit(task, amount="9000")
        except DomainError:
            return

    await asyncio.wait_for(asyncio.gather(issue(), edit()), 10)
    async with factory() as session:
        task = await TaskService(session).get(tid)
        svc = BillingService(session)
        period = await svc.get_period(pid)
        total = (await svc.totals(period)).invoice_total
    assert (period.status, total) == ("ISSUED", task.amount)


async def test_database_rejects_invalid_money_and_cascade_deletion(engine):
    from sqlalchemy import delete
    from sqlalchemy.exc import IntegrityError

    from app.models.client import Client

    factory, cid, _ = await seed(engine)
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            await session.execute(delete(Client).where(Client.id == cid))
    with pytest.raises(IntegrityError):
        async with factory.begin() as session:
            task = Task(
                client_id=cid, title="Invalid", amount=Decimal("-1"), currency="RUB", status="NEW"
            )
            session.add(task)
            await session.flush()
    async with factory() as session:
        assert await session.get(Client, cid) is not None


async def test_concurrent_new_work_after_issue_shares_one_new_draft(engine):
    from app.utils.time import current_month

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
        billing = BillingService(session)
        first = await billing.get_or_create_period(cid, *current_month("UTC"))
        await billing.add_manual_item(first.id, "First invoice", Decimal("19500"))
        await billing.issue(first)
        first_id = first.id
        tasks = TaskService(session)
        tids = [(await tasks.create(cid, title, amount="5000")).id for title in ("A", "B")]
    barrier = asyncio.Barrier(2)

    async def done(tid):
        async with factory.begin() as session:
            tasks = TaskService(session)
            task = await tasks.get(tid)
            await barrier.wait()
            await tasks.set_status(task, TaskStatus.DONE)
            return task.billing_period_id

    ids = await asyncio.wait_for(asyncio.gather(*(done(tid) for tid in tids)), 10)
    async with factory() as session:
        billing = BillingService(session)
        first = await billing.get_period(first_id)
        second = await billing.get_period(ids[0])
        assert ids[0] == ids[1] != first_id
        assert len(await billing.list_for_client(cid)) == 2
        assert (await billing.totals(first)).invoice_total == Decimal("19500")
        assert (await billing.totals(second)).invoice_total == Decimal("10000")


async def test_concurrent_issue_and_done_preserve_all_work(engine):
    from app.utils.time import current_month

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
        billing = BillingService(session)
        first = await billing.get_or_create_period(cid, *current_month("UTC"))
        await billing.add_manual_item(first.id, "First work", Decimal("19500"))
        pid = first.id
        tid = (await TaskService(session).create(cid, "New work", amount="5000")).id
    barrier = asyncio.Barrier(2)

    async def issue():
        async with factory.begin() as session:
            billing = BillingService(session)
            first = await billing.get_period(pid)
            await barrier.wait()
            await billing.issue(first)

    async def done():
        async with factory.begin() as session:
            tasks = TaskService(session)
            task = await tasks.get(tid)
            await barrier.wait()
            await tasks.set_status(task, TaskStatus.DONE)

    await asyncio.wait_for(asyncio.gather(issue(), done()), 10)
    async with factory() as session:
        billing = BillingService(session)
        invoices = await billing.list_for_client(cid)
        totals = [(await billing.totals(invoice)).invoice_total for invoice in invoices]
        assert sum(totals) == Decimal("24500")
        task = await TaskService(session).get(tid)
        matching = [
            i
            for invoice in invoices
            for i in await billing.items(invoice)
            if i.source_id == tid and i.source_type == "TASK"
        ]
        assert len(matching) == 1
        assert matching[0].billing_period_id == task.billing_period_id
        assert (await billing.get_period(pid)).status == "ISSUED"


async def transfer_seed(engine):
    from app.services.invoice_transfer_service import InvoiceTransferService

    factory, cid, source_id = await seed(engine)
    async with factory.begin() as session:
        billing = BillingService(session)
        target = await billing.get_or_create_period(cid, 2026, 9)
        await billing.add_manual_item(target.id, "Target", Decimal("1000"))
        await billing.issue(target)
        source = await billing.get_period(source_id)
        iid = (await billing.items(source))[0].id
        preview = await InvoiceTransferService(session).preview(target.id, [iid])
        return factory, cid, source_id, target.id, iid, preview.fingerprint


async def test_concurrent_invoice_transfer_replay_creates_one_result(engine):
    from app.models.billing import BillingPeriod, InvoiceItem
    from app.services.invoice_transfer_service import InvoiceTransferService

    factory, _, _, target, iid, fingerprint = await transfer_seed(engine)
    barrier = asyncio.Barrier(2)

    async def transfer():
        async with factory.begin() as session:
            await barrier.wait()
            return (
                await InvoiceTransferService(session).transfer(
                    target, [iid], key="same", expected=fingerprint
                )
            ).id

    ids = await asyncio.wait_for(asyncio.gather(transfer(), transfer()), 10)
    async with factory() as session:
        assert ids[0] == ids[1]
        assert await session.scalar(select(func.count()).select_from(BillingPeriod)) == 3
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InvoiceItem)
                .where(InvoiceItem.origin_item_id.is_not(None))
            )
            == 2
        )


async def test_payment_and_transfer_race_preserves_debt_and_history(engine):
    from app.services.dashboard_service import DashboardService
    from app.services.invoice_transfer_service import InvoiceTransferService

    factory, cid, source, target, iid, fingerprint = await transfer_seed(engine)
    barrier = asyncio.Barrier(2)

    async def transfer():
        try:
            async with factory.begin() as session:
                await barrier.wait()
                await InvoiceTransferService(session).transfer(
                    target, [iid], key="race", expected=fingerprint
                )
            return "transfer"
        except ValueError:
            return "rejected"

    async def pay():
        try:
            async with factory.begin() as session:
                await barrier.wait()
                await PaymentService(session).add_payment(source, "1000")
            return "payment"
        except DomainError:
            return "rejected"

    results = await asyncio.wait_for(asyncio.gather(transfer(), pay()), 10)
    async with factory() as session:
        paid = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)))
        debt = await DashboardService(session).total_debt(cid)
        assert results.count("rejected") == 1
        assert paid + debt == Decimal("11000")


@pytest.mark.parametrize("duplicate", [True, False])
async def test_concurrent_forward_group_is_one_task(engine, duplicate):
    from datetime import datetime, timezone

    from app.models.task import TaskForwardMessage
    from app.services.forward_task_service import ForwardTaskService

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        cid = (await ClientService(session).create("Forward client")).id
    barrier = asyncio.Barrier(2)
    received = datetime.now(timezone.utc)

    async def forward(key):
        async with factory.begin() as session:
            await barrier.wait()
            result = await ForwardTaskService(session).record(
                cid, SourceData(dedup_key=key, original_text=key, telegram_chat_id=111), received
            )
            return result.task.id

    ids = await asyncio.wait_for(
        asyncio.gather(forward("a"), forward("a" if duplicate else "b")), 10
    )
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(TaskForwardMessage))
        task_count = await session.scalar(select(func.count()).select_from(Task))
    assert ids[0] == ids[1]
    assert task_count == 1 and count == (1 if duplicate else 2)
