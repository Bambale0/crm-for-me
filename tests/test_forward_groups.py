from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.enums import TaskStatus
from app.models.task import Task, TaskForwardMessage
from app.services.client_service import ClientService
from app.services.forward_task_service import ForwardTaskService
from app.services.task_service import SourceData, TaskService

START = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)


def source(key, text=None, chat_id=111):
    return SourceData(
        dedup_key=key,
        original_text=text or key,
        telegram_chat_id=chat_id,
        telegram_message_id=int(key[-1]) if key[-1].isdigit() else 1,
    )


async def test_group_uses_gap_since_last_message_and_dedups_every_fragment(session):
    client = await ClientService(session).create("Client")
    svc = ForwardTaskService(session)
    first = await svc.record(client.id, source("first"), START)
    second = await svc.record(client.id, source("second"), START + timedelta(seconds=4))
    third = await svc.record(client.id, source("third"), START + timedelta(seconds=8))
    separate = await svc.record(client.id, source("separate"), START + timedelta(seconds=13))
    assert first.created and not second.created and not third.created and separate.created
    assert first.task.id == second.task.id == third.task.id != separate.task.id
    assert third.task.source.original_text == third.task.description == "first\n\nsecond\n\nthird"
    # A repeated middle fragment is still a duplicate after the window has closed.
    duplicate = await svc.record(client.id, source("second"), START + timedelta(seconds=60))
    assert duplicate.duplicate and duplicate.task.id == first.task.id
    assert await session.scalar(select(func.count()).select_from(Task)) == 2
    assert await session.scalar(select(func.count()).select_from(TaskForwardMessage)) == 4


@pytest.mark.parametrize("gap, same_task", [(4.999, True), (5, False), (5.001, False)])
async def test_forward_group_boundary(session, gap, same_task):
    client = await ClientService(session).create("Client")
    svc = ForwardTaskService(session)
    first = await svc.record(client.id, source("first"), START)
    second = await svc.record(client.id, source("second"), START + timedelta(seconds=gap))
    assert (first.task.id == second.task.id) is same_task


async def test_groups_do_not_mix_clients_chats_or_completed_tasks(session):
    first = await ClientService(session).create("First")
    other = await ClientService(session).create("Other")
    svc = ForwardTaskService(session)
    a = await svc.record(first.id, source("a"), START)
    b = await svc.record(other.id, source("b"), START)
    c = await svc.record(first.id, source("c", chat_id=222), START)
    await TaskService(session).set_status(a.task, TaskStatus.DONE)
    d = await svc.record(first.id, source("d"), START + timedelta(seconds=1))
    assert len({a.task.id, b.task.id, c.task.id, d.task.id}) == 4


async def test_group_survives_new_session_and_preserves_owner_edits(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        client = await ClientService(session).create("Client")
        result = await ForwardTaskService(session).record(client.id, source("first"), START)
        cid, tid = client.id, result.task.id
        await TaskService(session).edit(result.task, description="Owner note", amount="19500")
    async with factory.begin() as session:
        result = await ForwardTaskService(session).record(
            cid, source("second"), START + timedelta(seconds=2)
        )
        assert result.task.id == tid
        assert result.task.amount == 19500
        assert result.task.description == "Owner note\n\nsecond"
        assert result.task.source.original_text == "first\n\nsecond"
