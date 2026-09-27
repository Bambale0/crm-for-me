"""Shared ownership and transaction checks for a single-owner CRM."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.project import Project
from app.services.errors import NotFoundError


async def lock_client(session: AsyncSession, client_id: int) -> Client:
    # One lock order for every financial write: client -> task/period.
    client = await session.scalar(select(Client).where(Client.id == client_id).with_for_update())
    if client is None:
        raise NotFoundError("Клиент не найден")
    return client


async def validate_project(session: AsyncSession, client_id: int, project_id: int | None) -> None:
    await lock_client(session, client_id)
    if project_id is not None:
        project = await session.get(Project, project_id)
        if project is None or project.client_id != client_id:
            raise ValueError("Проект не принадлежит выбранному клиенту")
        if project.status == "ARCHIVED":
            raise ValueError("Проект архивирован")
