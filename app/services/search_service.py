"""Search service: case-insensitive lookup across clients, projects, tasks."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client, ClientField
from app.models.project import Project, ProjectField
from app.models.task import Task


@dataclass(frozen=True)
class SearchResult:
    clients: list[Client]
    projects: list[Project]
    tasks: list[Task]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(self, query: str) -> SearchResult:
        query = (query or "").strip()
        if not query:
            return SearchResult([], [], [])
        pattern = f"%{_escape_like(query)}%"

        clients = list((await self.session.scalars(
            select(Client)
            .where(
                or_(
                    Client.display_name.ilike(pattern),
                    Client.telegram_username.ilike(pattern),
                    Client.company_name.ilike(pattern),
                    Client.id.in_(
                        select(ClientField.client_id).where(ClientField.value.ilike(pattern))
                    ),
                )
            )
            .order_by(Client.display_name)
        )).all())

        projects = list((await self.session.scalars(
            select(Project)
            .where(
                or_(
                    Project.name.ilike(pattern),
                    Project.id.in_(
                        select(ProjectField.project_id).where(ProjectField.value.ilike(pattern))
                    ),
                )
            )
            .order_by(Project.name)
        )).all())

        tasks = list((await self.session.scalars(
            select(Task)
            .where(
                or_(
                    Task.title.ilike(pattern),
                    Task.description.ilike(pattern),
                )
            )
            .order_by(Task.created_at.desc())
        )).all())

        return SearchResult(clients=clients, projects=projects, tasks=tasks)
