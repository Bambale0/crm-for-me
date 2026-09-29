"""Client/project cards and creation; keep a forwarded task through project creation."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.project_service import ProjectService
from app.states import ClientCreation, ProjectCreation, TaskCreation
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
        f"👤 <b>{escape(client.display_name)}</b>",
        f"📅 {month_label(year, month)}: {format_money(stats.accrued)}",
        f"💰 Долг по всем счетам: {format_money(debt)}",
        f"Активных задач: {active}",
    ]
    if client.telegram_username:
        lines.append("@" + escape(client.telegram_username))
    if client.company_name:
        lines.append("🏢 " + escape(client.company_name))
    if client.comment:
        lines.append(escape(client.comment[:800]))
    if client.archived_at:
        lines.append("📦 Клиент в архиве")
    return "\n".join(lines)


@router.callback_query((F.data == kb.CLIENTS) | F.data.startswith("clients:"))
async def on_clients(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = query.data.split(":")
    page = int(parts[1]) if len(parts) > 1 else 0
    async with session_scope() as session:
        clients = await ClientService(session).list_active(limit=None)
    await query.answer()
    await query.message.edit_text(
        "Клиенты:" if clients else "Клиентов пока нет.", reply_markup=kb.clients_list(clients, page)
    )


@router.callback_query(F.data.startswith("client:"))
async def on_client_card(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    cid = int(query.data.split(":")[1])
    async with session_scope() as session:
        client = await ClientService(session).get(cid)
        text = await _client_card_text(session, client)
    await query.answer()
    await query.message.edit_text(text, reply_markup=kb.client_card(cid))


@router.callback_query(F.data == kb.CLIENT_NEW)
async def on_client_new(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ClientCreation.waiting_name)
    await query.answer()
    await query.message.edit_text("Введите имя клиента:", reply_markup=kb.back_to_main())


@router.message(ClientCreation.waiting_name)
async def on_client_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 255:
        await message.answer("Введите имя длиной от 1 до 255 символов.")
        return
    async with session_scope() as session:
        client = await ClientService(session).create(name)
    await state.clear()
    await message.answer("Клиент создан.", reply_markup=kb.client_card(client.id))


@router.callback_query(F.data.startswith("projects:"))
async def on_projects(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = query.data.split(":")
    cid, page = int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
    async with session_scope() as session:
        projects = await ProjectService(session).list_for_client(cid)
    await query.answer()
    await query.message.edit_text(
        "Проекты:" if projects else "Проектов пока нет.",
        reply_markup=kb.projects_list(cid, projects, page),
    )


@router.callback_query(F.data.startswith("project:"))
async def on_project(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    pid = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = ProjectService(session)
        project = await svc.get(pid)
    text = f"📁 <b>{escape(project.name)}</b>\nСтатус: {project.status}"
    if project.description:
        text += "\n" + escape(project.description[:1500])
    await query.answer()
    await query.message.edit_text(
        text, reply_markup=kb.project_actions(pid, project.client_id, project.status)
    )


@router.callback_query(F.data.startswith("project_new:"))
async def on_project_new(query: CallbackQuery, state: FSMContext) -> None:
    parts = query.data.split(":")
    cid = int(parts[1])
    in_task = len(parts) > 2 and parts[2] == "doz"
    if in_task and (await state.get_data()).get("doz_client_id") != cid:
        await query.answer("Начните дозаказ заново.", show_alert=True)
        return
    if not in_task:
        await state.clear()
    await state.update_data(project_client_id=cid, project_in_task=in_task)
    await state.set_state(ProjectCreation.waiting_name)
    await query.answer()
    await query.message.edit_text("Введите название проекта:", reply_markup=kb.back_to_main())


@router.message(ProjectCreation.waiting_name)
async def on_project_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 255:
        await message.answer("Введите название длиной от 1 до 255 символов.")
        return
    data = await state.get_data()
    cid = data["project_client_id"]
    async with session_scope() as session:
        project = await ProjectService(session).create(cid, name)
    if data.get("project_in_task"):
        await state.update_data(doz_project_id=project.id)
        await state.set_state(TaskCreation.waiting_amount)
        await message.answer(
            "Проект создан. Введите сумму дозаказа:", reply_markup=kb.back_to_main()
        )
    else:
        await state.clear()
        await message.answer("Проект создан.", reply_markup=kb.project_actions(project.id, cid))
