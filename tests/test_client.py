"""Client service tests."""

import pytest

from app.services.client_service import ClientService
from app.services.errors import AlreadyExistsError


async def test_create_client_without_telegram_id(session):
    svc = ClientService(session)
    client = await svc.create("Иван Петров")
    assert client.id is not None
    assert client.telegram_user_id is None
    assert client.display_name == "Иван Петров"


async def test_create_client_with_telegram_id(session):
    svc = ClientService(session)
    client = await svc.create("Мария", telegram_user_id=123456789, telegram_username="maria")
    assert client.telegram_user_id == 123456789


async def test_duplicate_telegram_id_rejected(session):
    svc = ClientService(session)
    await svc.create("Первый", telegram_user_id=111)
    with pytest.raises(AlreadyExistsError):
        await svc.create("Второй", telegram_user_id=111)


async def test_get_by_telegram_id(session):
    svc = ClientService(session)
    await svc.create("Первый", telegram_user_id=222)
    found = await svc.get_by_telegram_id(222)
    assert found is not None
    assert found.display_name == "Первый"
    assert await svc.get_by_telegram_id(999) is None


async def test_add_field(session):
    svc = ClientService(session)
    client = await svc.create("Компания")
    await svc.add_field(client.id, "email", "a@b.c")
    fields = await svc.list_fields(client.id)
    assert len(fields) == 1
    assert fields[0].value == "a@b.c"
