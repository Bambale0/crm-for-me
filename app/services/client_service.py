"""Client service: creation, lookup and custom fields."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client, ClientField
from app.repositories.client import ClientRepository
from app.services.errors import AlreadyExistsError, NotFoundError


class ClientService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ClientRepository(session)

    async def create(
        self,
        display_name: str,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
        company_name: str | None = None,
        comment: str | None = None,
    ) -> Client:
        display_name = (display_name or "").strip()
        if not display_name:
            raise ValueError("display_name is required")

        if telegram_user_id is not None:
            existing = await self.repo.get_by_telegram_id(telegram_user_id)
            if existing is not None:
                raise AlreadyExistsError(
                    f"Client with telegram_user_id={telegram_user_id} already exists"
                )

        client = Client(
            display_name=display_name,
            telegram_user_id=telegram_user_id,
            telegram_username=(telegram_username or None),
            company_name=(company_name or None),
            comment=(comment or None),
        )
        await self.repo.add(client)
        return client

    async def get(self, client_id: int) -> Client:
        client = await self.repo.get(client_id)
        if client is None:
            raise NotFoundError(f"Client {client_id} not found")
        return client

    async def get_by_telegram_id(self, telegram_user_id: int) -> Client | None:
        return await self.repo.get_by_telegram_id(telegram_user_id)

    async def list_active(self, offset: int = 0, limit: int = 20) -> list[Client]:
        return await self.repo.list_active(offset=offset, limit=limit)

    async def add_field(self, client_id: int, name: str, value: str) -> ClientField:
        await self.get(client_id)
        field = ClientField(client_id=client_id, name=name.strip(), value=value)
        return await self.repo.add_field(field)

    async def list_fields(self, client_id: int) -> list[ClientField]:
        return await self.repo.list_fields(client_id)
