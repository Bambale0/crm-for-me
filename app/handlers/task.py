"""Task handlers: list, status actions, and the minimal дозаказ flow."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.models.enums import TaskStatus
from app.services.project_service import ProjectService
from app.services.task_service import SourceData, TaskService
from app.states import TaskCreation
from app.utils.money import format_money, to_decimal

router = Router(name="task")


def _title_from_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "Дозаказ"
    first_line = text.splitlines()[0].strip()
    return first_line[:100] or "Дозаказ"


@router.callback_query(F.data.startswith(f"{kb.TASKS}:"))
async def on_tasks(query: CallbackQuery) -> None:
    parts = query.data.split(":")
    client_id = int(parts[1])
    context = "client"
    if len(parts) > 2 and parts[2] == "project":
        context = "project"
    async with session_scope() as session:
        svc = TaskService(session, tz_name=get_settings().timezone)
        tasks = await (svc.list_by_project(client_id) if context == "project" else svc.list_by_client(client_id))
    await query.message.edit_text(
        "Задачи:" if tasks else "Задач пока нет.",
        reply_markup=kb.tasks_list(client_id, tasks, context=context),
    )
    await query.answer()


@router.callback_query(F.data.startswith(f"{kb.TASK}:"))
async def on_task(query: CallbackQuery) -> None:
    task_id = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = TaskService(session, tz_name=get_settings().timezone)
        task = await svc.get(task_id)
        text = (
            f"📌 <b>{task.title}</b>\n"
            f"Статус: {task.status}\n"
            f"Сумма: {format_money(task.amount, task.currency)}"
        )
    await query.message.edit_text(text, reply_markup=kb.task_actions(task_id))
    await query.answer()


async def _set_status(query: CallbackQuery, task_id: int, status: TaskStatus) -> None:
    label = {"DONE": "✅ Выполнено", "IN_PROGRESS": "⏳ В работе", "CANCELLED": "❌ Отменено"}[status.value]
    async with session_scope() as session:
        svc = TaskService(session, tz_name=get_settings().timezone)
        task = await svc.get(task_id)
        await svc.set_status(task, status)
        text = f"📌 <b>{task.title}</b>\nСтатус: {task.status}"
    await query.answer(label)
    await query.message.edit_text(text, reply_markup=kb.task_actions(task_id))


@router.callback_query(F.data.startswith(f"{kb.TASK_DONE}:"))
async def on_task_done(query: CallbackQuery) -> None:
    await _set_status(query, int(query.data.split(":")[1]), TaskStatus.DONE)


@router.callback_query(F.data.startswith(f"{kb.TASK_PROGRESS}:"))
async def on_task_progress(query: CallbackQuery) -> None:
    await _set_status(query, int(query.data.split(":")[1]), TaskStatus.IN_PROGRESS)


@router.callback_query(F.data.startswith(f"{kb.TASK_CANCEL}:"))
async def on_task_cancel(query: CallbackQuery) -> None:
    await _set_status(query, int(query.data.split(":")[1]), TaskStatus.CANCELLED)


# ── дозаказ flow ───────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith(f"{kb.DOZ_START}:"))
async def on_doz_start(query: CallbackQuery, state: FSMContext) -> None:
    client_id = int(query.data.split(":")[1])
    await state.update_data(doz_client_id=client_id)
    async with session_scope() as session:
        projects = await ProjectService(session).list_for_client(client_id)
    await query.message.edit_text(
        "Выберите проект для дозаказа:",
        reply_markup=kb.doz_project_picker(client_id, projects),
    )
    await query.answer()


@router.callback_query(F.data.startswith(f"{kb.DOZ_PROJECT}:"))
async def on_doz_project(query: CallbackQuery, state: FSMContext) -> None:
    parts = query.data.split(":")
    client_id = int(parts[1])
    project_id = int(parts[2])
    await state.update_data(doz_client_id=client_id, doz_project_id=project_id or None)
    await state.set_state(TaskCreation.waiting_amount)
    await query.message.edit_text("Введите сумму дозаказа (например, 1500):")
    await query.answer()


@router.message(TaskCreation.waiting_amount)
async def on_doz_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = to_decimal(message.text or "")
    except Exception:
        await message.answer("Не удалось распознать сумму. Введите число, например: 1500")
        return

    data = await state.get_data()
    client_id = data.get("doz_client_id")
    project_id = data.get("doz_project_id")
    text = data.get("doz_text", "")
    title = _title_from_text(text)

    source = SourceData(
        telegram_chat_id=data.get("doz_chat_id"),
        telegram_message_id=data.get("doz_message_id"),
        forwarded_user_id=data.get("doz_forwarded_user_id"),
        original_text=text or None,
        dedup_key=f"chat:{data.get('doz_chat_id')}:msg:{data.get('doz_message_id')}",
    )

    async with session_scope() as session:
        svc = TaskService(session, tz_name=get_settings().timezone)
        try:
            task = await svc.create_unique_from_source(
                client_id,
                title,
                project_id=project_id,
                description=text or None,
                amount=amount,
                source=source,
            )
        except Exception as exc:
            await state.clear()
            await message.answer(f"Не удалось создать задачу: {exc}")
            return

    await state.clear()
    await message.answer(
        f"✅ Дозаказ создан:\n{task.title}\nСумма: {format_money(task.amount, task.currency)}",
        reply_markup=kb.client_card(client_id),
    )

