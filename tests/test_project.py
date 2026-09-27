"""Project service tests."""

from app.services.client_service import ClientService
from app.services.project_service import ProjectService


async def test_create_project(session):
    clients = ClientService(session)
    client = await clients.create("Заказчик")
    svc = ProjectService(session)
    project = await svc.create(client.id, "Сайт")
    assert project.client_id == client.id
    assert project.name == "Сайт"


async def test_list_projects_for_client(session):
    clients = ClientService(session)
    client = await clients.create("Заказчик")
    svc = ProjectService(session)
    await svc.create(client.id, "Сайт")
    await svc.create(client.id, "Бот")
    projects = await svc.list_for_client(client.id)
    assert {p.name for p in projects} == {"Сайт", "Бот"}
