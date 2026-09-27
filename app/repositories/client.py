"""Client and ClientField repositories."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client, ClientField


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, client_id: int) -> Client | None:
        return await self.session.get(Client, client_id)

    async def get_by_telegram_id(self, telegram_user_id: int) -> Client | None:
        stmt = select(Client).where(Client.telegram_user_id == telegram_user_id)
        return await self.session.scalar(stmt)

    async def list_active(self, offset: int = 0, limit: int = 20) -> list[Client]:
        stmt = (
            select(Client)
            .where(Client.archived_at.is_(None))
            .order_by(Client.display_name)
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def add(self, client: Client) -> Client:
        self.session.add(client)
        await self.session.flush()
        return client

    async def add_field(self, field: ClientField) -> ClientField:
        self.session.add(field)
        await self.session.flush()
        return field

    async def list_fields(self, client_id: int) -> list[ClientField]:
        stmt = (
            select(ClientField)
            .where(ClientField.client_id == client_id)
            .order_by(ClientField.sort_order, ClientField.id)
        )
        return list((await self.session.scalars(stmt)).all())
