"""Project service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ProjectStatus
from app.models.project import Project, ProjectField
from app.repositories.project import ProjectRepository
from app.services.errors import NotFoundError
from app.services.validation import lock_client
from app.utils.time import now_utc


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProjectRepository(session)

    async def create(self, client_id: int, name: str, description: str | None = None) -> Project:
        name = (name or "").strip()
        if not name or len(name) > 255:
            raise ValueError("name is required")
        await lock_client(self.session, client_id)
        project = Project(
            client_id=client_id,
            name=name,
            description=(description or None),
            status=ProjectStatus.ACTIVE.value,
        )
        await self.repo.add(project)
        return project

    async def get(self, project_id: int) -> Project:
        project = await self.repo.get(project_id)
        if project is None:
            raise NotFoundError(f"Project {project_id} not found")
        return project

    async def list_for_client(
        self, client_id: int, include_archived: bool = False
    ) -> list[Project]:
        return await self.repo.list_for_client(client_id, include_archived=include_archived)

    async def add_field(self, project_id: int, name: str, value: str) -> ProjectField:
        await self.get(project_id)
        if not name.strip() or len(name.strip()) > 255 or not value.strip():
            raise ValueError("Введите название и значение поля")
        field = ProjectField(project_id=project_id, name=name.strip(), value=value)
        return await self.repo.add_field(field)

    async def list_fields(self, project_id: int) -> list[ProjectField]:
        return await self.repo.list_fields(project_id)

    async def set_archived(self, entity_id: int, archived: bool) -> Project:
        entity = await self.get(entity_id)
        await lock_client(self.session, entity.client_id)
        entity.archived_at = (entity.archived_at or now_utc()) if archived else None
        entity.status = (
            ProjectStatus.ARCHIVED.value if entity.archived_at else ProjectStatus.ACTIVE.value
        )
        await self.session.flush()
        return entity
