"""Custom fields, notes and reversible archival for client/project cards."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.db import session_scope
from app.services.client_service import ClientService
from app.services.notes_service import NotesService
from app.services.project_service import ProjectService
from app.states import FieldFlow, NoteFlow

router = Router(name="details")


def service(session, kind: str):
    if kind == "client":
        return ClientService(session)
    if kind == "project":
        return ProjectService(session)
    raise ValueError("Неизвестная карточка")


@router.callback_query(F.data.startswith("details:"))
async def on_details(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = query.data.split(":")
    kind, eid = parts[1], int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    async with session_scope() as session:
        svc = service(session, kind)
        entity = await svc.get(eid)
        fields = await svc.list_fields(eid)
        notes_svc = NotesService(session)
        notes = await (
            notes_svc.list_client_notes(eid)
            if kind == "client"
            else notes_svc.list_project_notes(eid)
        )
    entries = [("field", f.id, f"{f.name}: {f.value}") for f in fields] + [
        ("note", n.id, n.text) for n in notes
    ]
    page = max(0, min(page, max(0, (len(entries) - 1) // 3)))
    lines = ["📝 Данные и заметки"] + [
        escape(e[2][:850]) for e in entries[page * 3 : (page + 1) * 3]
    ]
    if not entries:
        lines.append(
            "Пока пусто. Можно сохранить контакты, адрес сервера или ссылку на запись в менеджере паролей."
        )
    nav = []
    if page:
        nav.append(("⬅️", f"details:{kind}:{eid}:{page - 1}"))
    if (page + 1) * 3 < len(entries):
        nav.append(("➡️", f"details:{kind}:{eid}:{page + 1}"))
    rows = [nav] if nav else []
    rows.extend(
        [[("➕ Поле", f"field_new:{kind}:{eid}"), ("➕ Заметка", f"note_new:{kind}:{eid}")]]
    )
    archived = entity.archived_at is not None
    rows.append(
        [
            (
                "Вернуть из архива" if archived else "📦 В архив",
                f"archive_ask:{kind}:{eid}:{0 if archived else 1}",
            )
        ]
    )
    rows.append([("⬅️ Карточка", f"{kind}:{eid}")])
    await query.answer()
    await query.message.edit_text("\n\n".join(lines), reply_markup=kb.buttons(rows))


@router.callback_query(F.data.startswith("field_new:"))
async def on_field_new(query: CallbackQuery, state: FSMContext) -> None:
    _, kind, eid = query.data.split(":")
    await state.clear()
    await state.update_data(entity_kind=kind, entity_id=int(eid))
    await state.set_state(FieldFlow.name)
    await query.answer()
    await query.message.edit_text(
        "Название поля, например «Домен»:", reply_markup=kb.back_to_main()
    )


@router.message(FieldFlow.name)
async def on_field_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 255:
        raise ValueError("Название поля: от 1 до 255 символов")
    await state.update_data(field_name=name)
    await state.set_state(FieldFlow.value)
    await message.answer(
        "Введите значение. Для секретов укажите только ссылку на менеджер паролей.",
        reply_markup=kb.back_to_main(),
    )


@router.message(FieldFlow.value)
async def on_field_value(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value:
        raise ValueError("Введите значение поля")
    data = await state.get_data()
    async with session_scope() as session:
        await service(session, data["entity_kind"]).add_field(
            data["entity_id"], data["field_name"], value
        )
    await state.clear()
    await message.answer(
        "Поле сохранено.",
        reply_markup=kb.buttons(
            [[("Открыть данные", f"details:{data['entity_kind']}:{data['entity_id']}")]]
        ),
    )


@router.callback_query(F.data.startswith("note_new:"))
async def on_note_new(query: CallbackQuery, state: FSMContext) -> None:
    _, kind, eid = query.data.split(":")
    await state.clear()
    await state.update_data(entity_kind=kind, entity_id=int(eid))
    await state.set_state(NoteFlow.text)
    await query.answer()
    await query.message.edit_text("Введите заметку:", reply_markup=kb.back_to_main())


@router.message(NoteFlow.text)
async def on_note_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        raise ValueError("Введите текст заметки")
    data = await state.get_data()
    async with session_scope() as session:
        await service(session, data["entity_kind"]).get(data["entity_id"])
        svc = NotesService(session)
        if data["entity_kind"] == "client":
            await svc.add_client_note(data["entity_id"], text)
        else:
            await svc.add_project_note(data["entity_id"], text)
    await state.clear()
    await message.answer(
        "Заметка сохранена.",
        reply_markup=kb.buttons(
            [[("Открыть данные", f"details:{data['entity_kind']}:{data['entity_id']}")]]
        ),
    )


@router.callback_query(F.data.startswith("archive_ask:"))
async def on_archive_ask(query: CallbackQuery) -> None:
    parts = query.data.split(":")
    kind, eid = parts[1:3]
    archived = int(parts[3]) if len(parts) > 3 else 1
    await query.answer()
    await query.message.edit_text(
        "Изменить архивный статус карточки? Задачи, счета и оплаты сохранятся.",
        reply_markup=kb.buttons(
            [[("Подтвердить", f"archive:{kind}:{eid}:{archived}"), ("Назад", f"{kind}:{eid}")]]
        ),
    )


@router.callback_query(F.data.startswith("archive:"))
async def on_archive(query: CallbackQuery) -> None:
    _, kind, eid, archived = query.data.split(":")
    async with session_scope() as session:
        await service(session, kind).set_archived(int(eid), bool(int(archived)))
    await query.answer("Сохранено")
    await query.message.edit_text(
        "Статус изменён. Архивные карточки доступны через поиск.", reply_markup=kb.back_to_main()
    )
