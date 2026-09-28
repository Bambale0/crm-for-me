"""Persist a forwarded burst as one task, including every message's dedup key."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TaskStatus
from app.models.task import Task, TaskForwardMessage
from app.services.project_service import ProjectService
from app.services.task_service import SourceData, TaskService
from app.services.validation import lock_client


@dataclass(frozen=True)
class ForwardResult:
    task: Task
    created: bool
    duplicate: bool = False


class ForwardTaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskService(session)

    async def record(
        self, client_id: int, source: SourceData, received_at: datetime
    ) -> ForwardResult:
        if not source.dedup_key:
            raise ValueError("Forwarded message must have a dedup key")
        await lock_client(self.session, client_id)
        existing = await self.tasks.find_source_by_dedup(source.dedup_key)
        if existing:
            return ForwardResult(await self.tasks.get(existing.task_id), False, True)
        last = await self.session.scalar(
            select(TaskForwardMessage)
            .join(Task, Task.id == TaskForwardMessage.task_id)
            .where(
                Task.client_id == client_id,
                TaskForwardMessage.telegram_chat_id == source.telegram_chat_id,
            )
            .order_by(TaskForwardMessage.received_at.desc(), TaskForwardMessage.id.desc())
            .limit(1)
        )
        task = None
        if last and abs((received_at - last.received_at).total_seconds()) < 5:
            candidate = await self.tasks.get(last.task_id)
            if candidate.status in (TaskStatus.NEW.value, TaskStatus.IN_PROGRESS.value):
                task = candidate
        created = task is None
        text = source.original_text or ""
        if created:
            projects = await ProjectService(self.session).list_for_client(client_id)
            task = await self.tasks.create_unique_from_source(
                client_id,
                (text.strip().splitlines() or ["Пересланное сообщение"])[0][:100],
                project_id=projects[0].id if len(projects) == 1 else None,
                description=text or None,
                amount=Decimal("0"),
                source=source,
            )
        else:
            original = task.source.original_text or ""
            combined = "\n\n".join(part for part in (original, text) if part)
            task.source.original_text = combined or None
            # Keep owner edits; append the new fragment without replacing them.
            task.description = (
                combined or None
                if (task.description or "") == original
                else "\n\n".join(part for part in (task.description, text) if part) or None
            )
        self.session.add(
            TaskForwardMessage(
                task_id=task.id,
                dedup_key=source.dedup_key,
                received_at=received_at,
                telegram_chat_id=source.telegram_chat_id,
                telegram_message_id=source.telegram_message_id,
                original_text=source.original_text,
            )
        )
        await self.session.flush()
        await self.session.refresh(task, ["source"])
        return ForwardResult(task, created)
