"""Forwarded-message handler: resolves sender to a client card or prompt."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.forward_resolver import extract_forward_info
from app.utils.money import format_money
from app.utils.time import current_month, month_label

router = Router(name="forward")


def _new_client_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать клиента", callback_data="fwd_new_client")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data=kb.MAIN)],
        ]
    )


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


@router.message(F.forward_origin.is_not(None))
async def on_forward(message: Message, state: FSMContext) -> None:
    info = extract_forward_info(message)
    if info is None:
        await message.answer("Не удалось определить отправителя пересылки.")
        return

    text = message.text or message.caption or ""
    await state.update_data(
        doz_text=text,
        doz_chat_id=message.chat.id,
        doz_message_id=message.message_id,
        doz_forwarded_user_id=info.telegram_user_id,
        fwd_tg_id=info.telegram_user_id,
        fwd_name=info.display_name,
        fwd_username=info.username,
    )

    async with session_scope() as session:
        svc = ClientService(session)
        client = None
        if info.has_usable_id and info.telegram_user_id is not None:
            client = await svc.get_by_telegram_id(info.telegram_user_id)

        if client is None:
            await message.answer(
                f"Клиент не найден.\nОтправитель: {info.display_name}",
                reply_markup=_new_client_keyboard(),
            )
            return

        await message.answer(
            await _client_card_text(session, client),
            reply_markup=kb.client_card(client.id),
        )


@router.callback_query(F.data == "fwd_new_client")
async def on_fwd_new_client(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    name = data.get("fwd_name") or "Без имени"
    tg_id = data.get("fwd_tg_id")
    username = data.get("fwd_username")

    async with session_scope() as session:
        svc = ClientService(session)
        client = await svc.create(
            name,
            telegram_user_id=tg_id if tg_id else None,
            telegram_username=username,
        )
        text = await _client_card_text(session, client)

    await query.message.edit_text(text, reply_markup=kb.client_card(client.id))
    await query.answer("Клиент создан")
