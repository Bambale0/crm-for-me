"""Notes service (client and project notes)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import ClientNote
from app.models.project import ProjectNote


class NotesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_client_note(self, client_id: int, text: str) -> ClientNote:
        note = ClientNote(client_id=client_id, text=text)
        self.session.add(note)
        await self.session.flush()
        return note

    async def list_client_notes(self, client_id: int) -> list[ClientNote]:
        stmt = (
            select(ClientNote)
            .where(ClientNote.client_id == client_id)
            .order_by(ClientNote.created_at.desc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def add_project_note(self, project_id: int, text: str) -> ProjectNote:
        note = ProjectNote(project_id=project_id, text=text)
        self.session.add(note)
        await self.session.flush()
        return note

    async def list_project_notes(self, project_id: int) -> list[ProjectNote]:
        stmt = (
            select(ProjectNote)
            .where(ProjectNote.project_id == project_id)
            .order_by(ProjectNote.created_at.desc())
        )
        return list((await self.session.scalars(stmt)).all())
