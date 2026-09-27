"""Billing-period creation under an interleaved concurrent write.

Covers the concurrency requirement from AGENTS.md §19/§20: two workers may try to
create the same (client, year, month) period at the same moment, and a critical
state transition must not be lost.

Regression: BillingService.get_or_create_period used to call session.rollback()
when the INSERT lost the race, which discarded every pending change in the
caller's unit of work (e.g. the task being marked DONE).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (ensure all models are imported for metadata)
from app.db.base import Base
from app.models.billing import BillingPeriod
from app.models.enums import TaskStatus
from app.models.task import Task
from app.repositories.billing import BillingRepository
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.task_service import TaskService


@pytest_asyncio.fixture
async def engine(tmp_path):
    """File-backed engine so two sessions get real, independent connections."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'integrity.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_client(factory) -> int:
    async with factory() as session:
        client = await ClientService(session).create("Заказчик")
        await session.commit()
        return client.id


async def _commit_period_from_other_worker(factory, client_id: int) -> int:
    """Simulate the worker that wins the race and commits the period first."""
    async with factory() as other:
        period = BillingPeriod(client_id=client_id, year=2026, month=9, status="DRAFT")
        other.add(period)
        await other.commit()
        return period.id


def _make_read_stale(repo: BillingRepository) -> dict[str, int]:
    """Make the first lookup miss, exactly as a concurrent insert would."""
    real = BillingRepository.get_for_client_month
    calls = {"n": 0}

    async def stale_read(self, client_id: int, year: int, month: int):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real(self, client_id, year, month)

    repo.get_for_client_month = stale_read.__get__(repo, BillingRepository)
    return calls


async def test_lost_race_preserves_callers_pending_work(factory):
    client_id = await _seed_client(factory)
    await _commit_period_from_other_worker(factory, client_id)

    async with factory() as session:
        task = await TaskService(session, tz_name="UTC").create(
            client_id, "Работа", amount=Decimal("5000")
        )
        billing = BillingService(session)
        calls = _make_read_stale(billing.billing)

        period = await billing.get_or_create_period(client_id, 2026, 9)

        assert calls["n"] == 2, "the IntegrityError retry path was not exercised"
        assert period.id is not None
        # The caller's pending INSERT must survive the internal conflict.
        assert await session.scalar(select(func.count()).select_from(Task)) == 1
        assert task.id is not None


async def test_lost_race_preserves_done_transition(factory, monkeypatch):
    # This race targets the seeded September period regardless of CI wall clock.
    monkeypatch.setattr(
        "app.services.task_service.now_utc", lambda: datetime(2026, 9, 15, tzinfo=timezone.utc)
    )
    client_id = await _seed_client(factory)
    period_id = await _commit_period_from_other_worker(factory, client_id)

    async with factory() as session:
        tasks = TaskService(session, tz_name="UTC")
        task = await tasks.create(client_id, "Работа", amount=Decimal("5000"))
        _make_read_stale(tasks.billing.billing)

        await tasks.set_status(task, TaskStatus.DONE)
        await session.commit()
        task_id = task.id

    async with factory() as session:
        stored = await session.get(Task, task_id)
        assert stored is not None, "the completed task was lost"
        assert stored.status == TaskStatus.DONE.value
        assert stored.completed_at is not None
        assert stored.billing_period_id == period_id
