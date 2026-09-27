"""Owner reminders with an explicit local date and time."""

from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.services.reminder_service import ReminderService
from app.states import ReminderFlow
from app.utils.time import format_local, get_zone, now_utc

router = Router(name="reminder")


@router.callback_query(F.data.startswith("reminders:"))
async def on_reminders(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        reminders = await ReminderService(session).list_pending()
    await query.answer()
    await query.message.edit_text(
        "Напоминания владельцу:",
        reply_markup=kb.paged(
            [
                (
                    f"{format_local(r.remind_at, get_settings().timezone)} {r.text[:30]}",
                    f"reminder:{r.id}",
                )
                for r in reminders
            ],
            "reminders",
            int(query.data.split(":")[1]),
            [[("➕ Напоминание", "reminder_new"), ("🏠 В меню", kb.MAIN)]],
        ),
    )


@router.callback_query(F.data.startswith("reminder:"))
async def on_reminder(query: CallbackQuery) -> None:
    async with session_scope() as session:
        reminder = await ReminderService(session).get(int(query.data.split(":")[1]))
    await query.answer()
    await query.message.edit_text(
        f"⏰ {format_local(reminder.remind_at, get_settings().timezone)}\n{escape(reminder.text[:1500])}",
        reply_markup=kb.buttons(
            [
                [("Отменить напоминание", f"reminder_cancel:{reminder.id}")],
                [("⬅️ Назад", "reminders:0")],
            ]
        ),
    )


@router.callback_query(F.data == "reminder_new")
async def on_reminder_new(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ReminderFlow.text)
    await query.answer()
    await query.message.edit_text("О чём напомнить?", reply_markup=kb.back_to_main())


@router.message(ReminderFlow.text)
async def on_reminder_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not 1 <= len(text) <= 1500:
        raise ValueError("Текст напоминания: от 1 до 1500 символов")
    await state.update_data(reminder_text=text)
    await state.set_state(ReminderFlow.when)
    await message.answer(
        f"Когда? Формат: ГГГГ-ММ-ДД ЧЧ:ММ. Часовой пояс: {get_settings().timezone}.",
        reply_markup=kb.back_to_main(),
    )


@router.message(ReminderFlow.when)
async def on_reminder_when(message: Message, state: FSMContext) -> None:
    try:
        local = datetime.strptime(message.text or "", "%Y-%m-%d %H:%M").replace(
            tzinfo=get_zone(get_settings().timezone)
        )
        when = local.astimezone(timezone.utc)
    except ValueError:
        raise ValueError("Введите дату и время в формате ГГГГ-ММ-ДД ЧЧ:ММ") from None
    if when <= now_utc():
        raise ValueError("Выберите время в будущем")
    async with session_scope() as session:
        await ReminderService(session).create((await state.get_data())["reminder_text"], when)
    await state.clear()
    await message.answer(
        "Напоминание сохранено.", reply_markup=kb.buttons([[("Напоминания", "reminders:0")]])
    )


@router.callback_query(F.data.startswith("reminder_cancel:"))
async def on_reminder_cancel(query: CallbackQuery) -> None:
    async with session_scope() as session:
        svc = ReminderService(session)
        await svc.cancel(await svc.get(int(query.data.split(":")[1])))
    await query.answer("Отменено")
    await query.message.edit_text(
        "Напоминание отменено.", reply_markup=kb.buttons([[("Напоминания", "reminders:0")]])
    )
