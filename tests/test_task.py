"""Task workflow tests: transitions, idempotency, dedup."""

from decimal import Decimal

import pytest

from app.models.enums import TaskStatus
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.errors import InvalidTransitionError
from app.services.task_service import SourceData, TaskService


async def _client(session):
    return await ClientService(session).create("Заказчик")


async def test_new_task_is_new(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Задача")
    assert task.status == TaskStatus.NEW.value


async def test_new_to_done_sets_completed_at(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Задача", amount=Decimal("1000"))
    task = await svc.set_status(task, TaskStatus.DONE)
    assert task.status == TaskStatus.DONE.value
    assert task.completed_at is not None
    assert task.billing_period_id is not None


async def test_done_is_idempotent_single_billing_item(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Задача", amount=Decimal("1000"))
    await svc.set_status(task, TaskStatus.DONE)
    first_completed = task.completed_at
    # Second "Done" click must be a no-op.
    await svc.set_status(task, TaskStatus.DONE)
    assert task.completed_at == first_completed

    billing = BillingService(session)
    period = await billing.get_period(task.billing_period_id)
    items = await billing.items(period)
    task_items = [i for i in items if i.source_type == "TASK"]
    assert len(task_items) == 1


async def test_invalid_transition_rejected(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Задача")
    await svc.set_status(task, TaskStatus.DONE)
    with pytest.raises(InvalidTransitionError):
        await svc.set_status(task, TaskStatus.NEW)


async def test_forward_dedup_key(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    source = SourceData(
        telegram_chat_id=1,
        telegram_message_id=2,
        forwarded_user_id=333,
        original_text="текст",
        dedup_key="chat:1:msg:2",
    )
    await svc.create(client.id, "Задача", source=source)
    existing = await svc.find_source_by_dedup("chat:1:msg:2")
    assert existing is not None
    assert existing.forwarded_user_id == 333


async def test_cancel_unassigns_draft_item(session):
    client = await _client(session)
    svc = TaskService(session, tz_name="UTC")
    task = await svc.create(client.id, "Задача", amount=Decimal("500"))
    task = await svc.set_status(task, TaskStatus.IN_PROGRESS)
    task = await svc.set_status(task, TaskStatus.DONE)
    # Can't cancel after DONE (no transition), so test cancel from IN_PROGRESS.
    task2 = await svc.create(client.id, "Другая", amount=Decimal("500"))
    await svc.set_status(task2, TaskStatus.IN_PROGRESS)
    await svc.set_status(task2, TaskStatus.CANCELLED)
    assert task2.billing_period_id is None
    assert task2.cancelled_at is not None
