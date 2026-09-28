"""Task workflow and fast forwarded/manual order creation."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.models.enums import TaskStatus
from app.services.client_service import ClientService
from app.services.project_service import ProjectService
from app.services.task_service import SourceData, TaskService
from app.services.validation import validate_project
from app.states import EditFlow, TaskCreation
from app.utils.money import format_money, to_decimal

router = Router(name="task")


def _title_from_text(text: str) -> str:
    return ((text or "").strip().splitlines() or ["Дозаказ"])[0][:100] or "Дозаказ"


def task_text(task) -> str:
    labels = {
        "NEW": "Новая",
        "IN_PROGRESS": "В работе",
        "DONE": "Выполнена",
        "CANCELLED": "Отменена",
    }
    lines = [
        f"📌 <b>{escape(task.title)}</b>",
        f"Статус: {labels[task.status]}",
        f"Сумма: {format_money(task.amount, task.currency)}",
    ]
    if task.description:
        lines.append(escape(task.description[:800]))
    if task.source and task.source.original_text:
        lines.append("\nИсходное сообщение:\n" + escape(task.source.original_text[:800]))
    return "\n".join(lines)


async def _task_context(session, entity_id: int, context: str) -> tuple[int, int | None, str, bool]:
    if context == "project":
        project = await ProjectService(session).get(entity_id)
        client = await ClientService(session).get(project.client_id)
        return (
            client.id,
            project.id,
            f"{client.display_name} / {project.name}",
            project.status != "ARCHIVED",
        )
    if context != "client":
        raise ValueError("Откройте список задач заново")
    client = await ClientService(session).get(entity_id)
    return client.id, None, client.display_name, True


async def _quick_context(
    state: FSMContext, cid: int, pid: int | None, label: str, back: str
) -> None:
    await state.clear()
    await state.update_data(
        doz_client_id=cid, doz_project_id=pid, quick_label=label, quick_back=back
    )
    await state.set_state(TaskCreation.quick_title)


def _quick_cancel(data: dict):
    return kb.buttons([[("Отмена", data["quick_back"])]])


@router.callback_query(F.data.startswith("tasks:"))
async def on_tasks(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = query.data.split(":")
    entity_id = int(parts[1])
    context = parts[2] if len(parts) > 2 else "client"
    page = int(parts[3]) if len(parts) > 3 else 0
    async with session_scope() as session:
        cid, pid, label, can_create = await _task_context(session, entity_id, context)
        svc = TaskService(session, get_settings().timezone)
        tasks = await (svc.list_by_project(pid) if pid is not None else svc.list_by_client(cid))
    if can_create:
        await _quick_context(state, cid, pid, label, f"tasks:{entity_id}:{context}:{page}")
    text = ("Задачи:" if tasks else "Задач пока нет.") + f"\n{escape(label)}"
    text += (
        "\n\nНапишите название новой задачи или нажмите «➕ Добавить задачу»."
        if can_create
        else "\n\nПроект в архиве. Создание новых задач недоступно."
    )
    await query.answer()
    await query.message.edit_text(
        text, reply_markup=kb.tasks_list(entity_id, tasks, context, page, can_create=can_create)
    )


@router.callback_query(F.data.startswith("task_new:"))
async def on_task_new(query: CallbackQuery, state: FSMContext) -> None:
    _, entity_id, context = query.data.split(":")
    async with session_scope() as session:
        cid, pid, label, can_create = await _task_context(session, int(entity_id), context)
        if not can_create:
            raise ValueError("Проект архивирован")
    await _quick_context(state, cid, pid, label, f"tasks:{entity_id}:{context}")
    await query.answer()
    await query.message.edit_text(
        f"{escape(label)}\nВведите название задачи:",
        reply_markup=_quick_cancel(await state.get_data()),
    )


@router.message(TaskCreation.quick_title)
async def on_quick_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not 1 <= len(title) <= 500 or title.startswith("/"):
        raise ValueError("Введите название задачи текстом, от 1 до 500 символов")
    data = await state.get_data()
    async with session_scope() as session:
        await validate_project(session, data["doz_client_id"], data.get("doz_project_id"))
    await state.update_data(
        quick_title=title, doz_chat_id=message.chat.id, doz_message_id=message.message_id
    )
    await state.set_state(TaskCreation.quick_amount)
    await message.answer(
        f"{escape(data['quick_label'])}\nЗадача: {escape(title)}\n\nВведите сумму задачи в ₽. Если бесплатно — 0:",
        reply_markup=_quick_cancel(data),
    )


@router.message(TaskCreation.quick_amount)
async def on_quick_amount(message: Message, state: FSMContext) -> None:
    amount = to_decimal(message.text or "")
    data = await state.get_data()
    await _save_task(message, state, amount, data["quick_title"])


@router.callback_query(F.data.startswith("task:"))
async def on_task(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        task = await TaskService(session).get(int(query.data.split(":")[1]))
        text = task_text(task)
    await query.answer()
    await query.message.edit_text(
        text,
        reply_markup=kb.task_actions(
            task.id, task.status, task.client_id, project_id=task.project_id
        ),
    )


async def _set_status(query: CallbackQuery, state: FSMContext, status: TaskStatus) -> None:
    await state.clear()
    async with session_scope() as session:
        svc = TaskService(session, get_settings().timezone)
        task = await svc.get(int(query.data.split(":")[1]))
        await svc.set_status(task, status)
        text = task_text(task)
    await query.answer("Сохранено")
    await query.message.edit_text(
        text,
        reply_markup=kb.task_actions(
            task.id, task.status, task.client_id, project_id=task.project_id
        ),
    )


@router.callback_query(F.data.startswith("task_done:"))
async def on_task_done(query: CallbackQuery, state: FSMContext) -> None:
    await _set_status(query, state, TaskStatus.DONE)


@router.callback_query(F.data.startswith("task_progress:"))
async def on_task_progress(query: CallbackQuery, state: FSMContext) -> None:
    await _set_status(query, state, TaskStatus.IN_PROGRESS)


@router.callback_query(F.data.startswith("task_cancel_ask:"))
async def on_task_cancel_ask(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(query.data.split(":")[1])
    await query.answer()
    await query.message.edit_text(
        "Отменить задачу?",
        reply_markup=kb.buttons(
            [[("Да, отменить", f"task_cancel:{tid}"), ("Назад", f"task:{tid}")]]
        ),
    )


@router.callback_query(F.data.startswith("task_cancel:"))
async def on_task_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await _set_status(query, state, TaskStatus.CANCELLED)


@router.callback_query(F.data.startswith("task_edit:"))
async def on_task_edit(query: CallbackQuery, state: FSMContext) -> None:
    _, tid, field = query.data.split(":")
    if field not in ("title", "description", "amount"):
        raise ValueError("Неизвестное поле")
    await state.clear()
    await state.update_data(edit_task_id=int(tid), edit_field=field)
    await state.set_state(EditFlow.value)
    await query.answer()
    await query.message.edit_text("Введите новое значение:", reply_markup=kb.back_to_main())


@router.message(EditFlow.value)
async def on_task_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    value = (message.text or "").strip()
    field = data["edit_field"]
    if not value or (field == "title" and len(value) > 500):
        raise ValueError("Введите корректное значение")
    async with session_scope() as session:
        svc = TaskService(session)
        task = await svc.get(data["edit_task_id"])
        await svc.edit(task, **{field: to_decimal(value) if field == "amount" else value})
    await state.clear()
    await message.answer(
        "Изменения сохранены.",
        reply_markup=kb.task_actions(
            task.id, task.status, task.client_id, project_id=task.project_id
        ),
    )


@router.callback_query(F.data.startswith("doz_start:"))
async def on_doz_start(query: CallbackQuery, state: FSMContext) -> None:
    parts = query.data.split(":")
    cid = int(parts[1])
    data = await state.get_data()
    if len(parts) > 2:
        if data.get("fwd_token") != parts[2] or data.get("fwd_client_id") != cid:
            await query.answer("Перешлите сообщение заново.", show_alert=True)
            return
    else:
        await state.clear()
    await state.update_data(doz_client_id=cid)
    await state.set_state(TaskCreation.waiting_project)
    async with session_scope() as session:
        await validate_project(session, cid, None)
        projects = await ProjectService(session).list_for_client(cid)
    await query.answer()
    await query.message.edit_text(
        "Выберите проект:", reply_markup=kb.doz_project_picker(cid, projects)
    )


@router.callback_query(TaskCreation.waiting_project, F.data.startswith("doz_page:"))
async def on_doz_page(query: CallbackQuery, state: FSMContext) -> None:
    _, cid, page = query.data.split(":")
    if (await state.get_data()).get("doz_client_id") != int(cid):
        raise ValueError("Начните дозаказ заново")
    async with session_scope() as session:
        projects = await ProjectService(session).list_for_client(int(cid))
    await query.answer()
    await query.message.edit_text(
        "Выберите проект:", reply_markup=kb.doz_project_picker(int(cid), projects, int(page))
    )


@router.callback_query(TaskCreation.waiting_project, F.data.startswith("doz_project:"))
async def on_doz_project(query: CallbackQuery, state: FSMContext) -> None:
    _, cid, pid = query.data.split(":")
    cid, pid = int(cid), int(pid) or None
    if (await state.get_data()).get("doz_client_id") != cid:
        raise ValueError("Начните дозаказ заново")
    async with session_scope() as session:
        await validate_project(session, cid, pid)
    await state.update_data(doz_project_id=pid)
    await state.set_state(TaskCreation.waiting_amount)
    await query.answer()
    await query.message.edit_text(
        "Введите сумму дозаказа, например 1500:", reply_markup=kb.back_to_main()
    )


async def _save_task(message: Message, state: FSMContext, amount, title: str) -> None:
    data = await state.get_data()
    source = SourceData.from_message(
        chat_id=data.get("doz_chat_id"),
        message_id=data.get("doz_message_id"),
        forwarded_user_id=data.get("doz_forwarded_user_id"),
        original_text=data.get("doz_text") or data.get("quick_title"),
        source_type="TELEGRAM_FORWARD" if data.get("doz_dedup_key") else "MANUAL",
    )
    # Manual entry is also durable against a redelivered final input message.
    from dataclasses import replace

    source = replace(
        source,
        dedup_key=data.get("doz_dedup_key") or f"manual:{message.chat.id}:{message.message_id}",
    )
    async with session_scope() as session:
        svc = TaskService(session, get_settings().timezone)
        task = await svc.create_unique_from_source(
            data["doz_client_id"],
            title,
            project_id=data.get("doz_project_id"),
            description=data.get("doz_text") or None,
            amount=amount,
            source=source,
        )
    await state.clear()
    await message.answer(
        f"✅ Создано: {escape(task.title)}\n{format_money(task.amount)}",
        reply_markup=kb.task_actions(
            task.id, task.status, task.client_id, project_id=task.project_id
        ),
    )


@router.message(TaskCreation.waiting_amount)
async def on_doz_amount(message: Message, state: FSMContext) -> None:
    amount = to_decimal(message.text or "")
    data = await state.get_data()
    if not data.get("doz_text"):
        await state.update_data(doz_amount=str(amount))
        await state.set_state(TaskCreation.waiting_title)
        await message.answer("Введите название задачи:", reply_markup=kb.back_to_main())
        return
    await _save_task(message, state, amount, _title_from_text(data["doz_text"]))


@router.message(TaskCreation.waiting_title)
async def on_doz_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not 1 <= len(title) <= 500:
        raise ValueError("Введите название длиной от 1 до 500 символов")
    await _save_task(message, state, to_decimal((await state.get_data())["doz_amount"]), title)
