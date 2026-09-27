"""Client and project handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.project_service import ProjectService
from app.states import ClientCreation, ProjectCreation
from app.utils.money import format_money
from app.utils.time import current_month, month_label

router = Router(name="client")


async def _client_card_text(session, client) -> str:
    tz = get_settings().timezone
    dash = DashboardService(session, tz_name=tz)
    year, month = current_month(tz)
    stats = await dash.client_month_stats(client.id, year, month)
    debt = await dash.total_debt(client.id)
    active = await dash.active_task_count(client.id)
    lines = [
        f"👤 <b>{client.display_name}</b>",
        f"📅 {month_label(year, month)}: {format_money(stats.accrued, 'RUB')}",
        f"💰 Долг: {format_money(debt, 'RUB')}",
        f"✅ Активных задач: {active}",
    ]
    if client.telegram_username:
        lines.append(f"@{client.telegram_username}")
    if client.company_name:
        lines.append(f"🏢 {client.company_name}")
    return "\n".join(lines)


@router.callback_query(F.data == kb.CLIENTS)
async def on_clients(query: CallbackQuery) -> None:
    async with session_scope() as session:
        clients = await ClientService(session).list_active(limit=50)
    await query.message.edit_text(
        "Клиенты:" if clients else "Клиентов пока нет.", reply_markup=kb.clients_list(clients)
    )
    await query.answer()


@router.callback_query(F.data.startswith(f"{kb.CLIENT}:"))
async def on_client_card(query: CallbackQuery) -> None:
    client_id = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = ClientService(session)
        client = await svc.get(client_id)
        text = await _client_card_text(session, client)
    await query.message.edit_text(text, reply_markup=kb.client_card(client_id))
    await query.answer()


@router.callback_query(F.data == kb.CLIENT_NEW)
async def on_client_new(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ClientCreation.waiting_name)
    await query.message.edit_text("Введите имя клиента:")
    await query.answer()


@router.message(ClientCreation.waiting_name)
async def on_client_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не может быть пустым.")
        return
    async with session_scope() as session:
        client = await ClientService(session).create(name)
    await state.clear()
    await message.answer("Клиент создан.", reply_markup=kb.client_card(client.id))


# ── projects ───────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith(f"{kb.PROJECTS}:"))
async def on_projects(query: CallbackQuery) -> None:
    client_id = int(query.data.split(":")[1])
    async with session_scope() as session:
        projects = await ProjectService(session).list_for_client(client_id)
    await query.message.edit_text(
        "Проекты:" if projects else "Проектов пока нет.",
        reply_markup=kb.projects_list(client_id, projects),
    )
    await query.answer()


@router.callback_query(F.data.startswith(f"{kb.PROJECT_NEW}:"))
async def on_project_new(query: CallbackQuery, state: FSMContext) -> None:
    client_id = int(query.data.split(":")[1])
    await state.update_data(project_client_id=client_id)
    await state.set_state(ProjectCreation.waiting_name)
    await query.message.edit_text("Введите название проекта:")
    await query.answer()


@router.message(ProjectCreation.waiting_name)
async def on_project_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    data = await state.get_data()
    client_id = data["project_client_id"]
    async with session_scope() as session:
        await ProjectService(session).create(client_id, name)
    await state.clear()
    await message.answer("Проект создан.", reply_markup=kb.client_card(client_id))
