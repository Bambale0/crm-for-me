"""Task service: workflow transitions, dedup and billing assignment."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TASK_TRANSITIONS, BillingStatus, TaskStatus
from app.models.task import Task, TaskSource
from app.repositories.task import TaskRepository
from app.services.billing_service import BillingService
from app.services.errors import (
    AlreadyExistsError,
    InvalidTransitionError,
    InvoiceIssuedError,
    NotFoundError,
)
from app.utils.money import quantize
from app.utils.time import local_month, now_utc


@dataclass(frozen=True)
class SourceData:
    """Telegram metadata for the original forwarded message."""

    source_type: str = "TELEGRAM_FORWARD"
    telegram_chat_id: int | None = None
    telegram_message_id: int | None = None
    forwarded_user_id: int | None = None
    original_text: str | None = None
    dedup_key: str | None = None

    @classmethod
    def from_message(
        cls,
        *,
        chat_id: int | None,
        message_id: int | None,
        forwarded_user_id: int | None = None,
        original_text: str | None = None,
        source_type: str = "TELEGRAM_FORWARD",
    ) -> "SourceData":
        """Build source metadata from a Telegram message.

        A dedup key is derived only when the message is identifiable. A дозаказ
        started from the client card carries no chat/message id, so it must stay
        keyless rather than collapsing onto a shared sentinel value.
        """
        dedup_key = (
            f"chat:{chat_id}:msg:{message_id}"
            if chat_id is not None and message_id is not None
            else None
        )
        return cls(
            source_type=source_type,
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
            forwarded_user_id=forwarded_user_id,
            original_text=(original_text or None),
            dedup_key=dedup_key,
        )


class TaskService:
    def __init__(self, session: AsyncSession, tz_name: str = "UTC") -> None:
        self.session = session
        self.tz = tz_name
        self.repo = TaskRepository(session)
        self.billing = BillingService(session)

    async def create(
        self,
        client_id: int,
        title: str,
        *,
        project_id: int | None = None,
        description: str | None = None,
        amount: Decimal | str = Decimal("0"),
        currency: str = "RUB",
        source: SourceData | None = None,
    ) -> Task:
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        task = Task(
            client_id=client_id,
            project_id=project_id,
            title=title,
            description=(description or None),
            amount=quantize(Decimal(str(amount))),
            currency=currency,
            status=TaskStatus.NEW.value,
        )
        await self.repo.add(task)
        if source is not None:
            await self._attach_source(task, source)
        return task

    async def _attach_source(self, task: Task, source: SourceData) -> TaskSource:
        ts = TaskSource(
            task_id=task.id,
            source_type=source.source_type,
            telegram_chat_id=source.telegram_chat_id,
            telegram_message_id=source.telegram_message_id,
            forwarded_user_id=source.forwarded_user_id,
            original_text=source.original_text,
            dedup_key=source.dedup_key,
        )
        await self.repo.add_source(ts)
        return ts

    async def find_source_by_dedup(self, dedup_key: str) -> TaskSource | None:
        if not dedup_key:
            return None
        return await self.repo.find_source_by_dedup(dedup_key)

    async def create_unique_from_source(
        self,
        client_id: int,
        title: str,
        *,
        project_id: int | None = None,
        description: str | None = None,
        amount: Decimal | str = Decimal("0"),
        currency: str = "RUB",
        source: SourceData,
    ) -> Task:
        """Create a task, refusing to duplicate a task for the same source.

        Protects against double-processing a forwarded message (e.g. double
        callback). Raises AlreadyExistsError when the dedup key is already used.
        """
        if source.dedup_key:
            existing = await self.find_source_by_dedup(source.dedup_key)
            if existing is not None:
                raise AlreadyExistsError(
                    f"Task already exists for source {source.dedup_key}"
                )
        return await self.create(
            client_id,
            title,
            project_id=project_id,
            description=description,
            amount=amount,
            currency=currency,
            source=source,
        )

    async def get(self, task_id: int) -> Task:
        task = await self.repo.get(task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id} not found")
        return task

    async def set_status(self, task: Task, new_status: TaskStatus) -> Task:
        current = TaskStatus(task.status)
        if current == new_status:
            return task  # idempotent

        if new_status not in TASK_TRANSITIONS[current]:
            raise InvalidTransitionError(f"Cannot transition {current} -> {new_status}")

        task.status = new_status.value
        now = now_utc()

        if new_status == TaskStatus.IN_PROGRESS:
            if task.started_at is None:
                task.started_at = now
        elif new_status == TaskStatus.DONE:
            if task.started_at is None:
                task.started_at = now
            task.completed_at = now
            await self._assign_to_period(task)
        elif new_status == TaskStatus.CANCELLED:
            task.cancelled_at = now
            await self._unassign_from_period(task)

        await self.session.flush()
        return task

    async def _assign_to_period(self, task: Task) -> None:
        year, month = local_month(task.completed_at, self.tz)
        period = await self.billing.get_or_create_period(task.client_id, year, month)
        if period.status != BillingStatus.DRAFT.value:
            # The month's invoice is already issued; its items are a frozen
            # snapshot. Booking the task here would silently drop the revenue,
            # so refuse the transition and let the owner decide what to do.
            raise InvoiceIssuedError(
                f"Расчётный период {month:02d}.{year} уже выставлен "
                f"(status={period.status}); задача не может быть закрыта в этот счёт"
            )
        task.billing_period_id = period.id
        await self.session.flush()
        await self.billing.reconcile_draft(period)

    async def _unassign_from_period(self, task: Task) -> None:
        if task.billing_period_id is None:
            return
        period = await self.billing.get_period(task.billing_period_id)
        task.billing_period_id = None
        await self.session.flush()
        if period.status == BillingStatus.DRAFT.value:
            await self.billing.reconcile_draft(period)

    async def edit(
        self,
        task: Task,
        *,
        title: str | None = None,
        description: str | None = None,
        amount: Decimal | str | None = None,
        project_id: int | None = None,
    ) -> Task:
        if amount is not None:
            await self._ensure_amount_editable(task)

        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("title cannot be empty")
            task.title = title
        if description is not None:
            task.description = description
        if amount is not None:
            task.amount = quantize(Decimal(str(amount)))
        if project_id is not None:
            task.project_id = project_id

        await self.session.flush()

        if task.billing_period_id is not None:
            period = await self.billing.get_period(task.billing_period_id)
            if period.status == BillingStatus.DRAFT.value:
                await self.billing.reconcile_draft(period)
        return task

    async def _ensure_amount_editable(self, task: Task) -> None:
        if task.billing_period_id is None:
            return
        period = await self.billing.get_period(task.billing_period_id)
        if period.status != BillingStatus.DRAFT.value:
            raise InvoiceIssuedError(
                f"Task {task.id} belongs to a {period.status} invoice; amount is frozen"
            )

    async def list_by_client(self, client_id: int, status: TaskStatus | None = None) -> list[Task]:
        return await self.repo.list_by_client(client_id, status=status)

    async def list_by_project(self, project_id: int) -> list[Task]:
        return await self.repo.list_by_project(project_id)

    async def list_all(self, status: TaskStatus | None = None) -> list[Task]:
        return await self.repo.list_all(status=status)

