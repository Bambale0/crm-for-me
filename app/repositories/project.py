"""Project and ProjectField repositories."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ProjectStatus
from app.models.project import Project, ProjectField


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, project_id: int) -> Project | None:
        return await self.session.get(Project, project_id)

    async def list_for_client(
        self, client_id: int, include_archived: bool = False
    ) -> list[Project]:
        stmt = select(Project).where(Project.client_id == client_id).order_by(Project.name)
        if not include_archived:
            stmt = stmt.where(Project.status != ProjectStatus.ARCHIVED.value)
        return list((await self.session.scalars(stmt)).all())

    async def add(self, project: Project) -> Project:
        self.session.add(project)
        await self.session.flush()
        return project

    async def add_field(self, field: ProjectField) -> ProjectField:
        self.session.add(field)
        await self.session.flush()
        return field

    async def list_fields(self, project_id: int) -> list[ProjectField]:
        stmt = (
            select(ProjectField)
            .where(ProjectField.project_id == project_id)
            .order_by(ProjectField.sort_order, ProjectField.id)
        )
        return list((await self.session.scalars(stmt)).all())
