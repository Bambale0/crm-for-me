"""Task and TaskSource repositories."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TaskStatus
from app.models.task import Task, TaskSource


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, task_id: int) -> Task | None:
        return await self.session.get(Task, task_id)

    async def add(self, task: Task) -> Task:
        self.session.add(task)
        await self.session.flush()
        return task

    async def list_by_client(
        self, client_id: int, status: TaskStatus | None = None
    ) -> list[Task]:
        stmt = select(Task).where(Task.client_id == client_id).order_by(Task.created_at.desc())
        if status is not None:
            stmt = stmt.where(Task.status == status.value)
        return list((await self.session.scalars(stmt)).all())

    async def list_by_project(self, project_id: int) -> list[Task]:
        stmt = (
            select(Task).where(Task.project_id == project_id).order_by(Task.created_at.desc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def list_all(self, status: TaskStatus | None = None) -> list[Task]:
        stmt = select(Task).order_by(Task.created_at.desc())
        if status is not None:
            stmt = stmt.where(Task.status == status.value)
        return list((await self.session.scalars(stmt)).all())

    async def find_source_by_dedup(self, dedup_key: str) -> TaskSource | None:
        stmt = select(TaskSource).where(TaskSource.dedup_key == dedup_key)
        return await self.session.scalar(stmt)

    async def add_source(self, source: TaskSource) -> TaskSource:
        self.session.add(source)
        await self.session.flush()
        return source
