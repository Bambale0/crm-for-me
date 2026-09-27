"""Resolve reliable sender IDs; hidden senders require manual selection."""

from hashlib import sha256
from html import escape
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, MessageOriginChannel, MessageOriginUser

from app import keyboards as kb
from app.db import session_scope
from app.handlers.client import _client_card_text
from app.services.client_service import ClientService
from app.services.forward_resolver import extract_forward_info
from app.services.task_service import TaskService

router = Router(name="forward")


def source_key(message: Message) -> str:
    origin = message.forward_origin
    # User forward dates identify the original event across separate forwards.
    # Hidden names are not reliable identity; only dedup the delivered update.
    if isinstance(origin, (MessageOriginUser, MessageOriginChannel)):
        if isinstance(origin, MessageOriginChannel):
            return f"channel:{origin.chat.id}:{origin.message_id}"
        payload = f"user:{origin.sender_user.id}:{int(origin.date.timestamp())}:" + (
            message.text or message.caption or ""
        )
        media = message.document or message.video or (message.photo[-1] if message.photo else None)
        if media:
            payload += media.file_unique_id
        return "forward:" + sha256(payload.encode()).hexdigest()
    return f"chat:{message.chat.id}:msg:{message.message_id}"


@router.message(F.forward_origin.is_not(None))
async def on_forward(message: Message, state: FSMContext) -> None:
    info = extract_forward_info(message)
    if info is None:
        return
    await state.clear()
    key = source_key(message)
    async with session_scope() as session:
        existing = await TaskService(session).find_source_by_dedup(key)
        if existing:
            await message.answer(
                "Из этого сообщения уже создана задача.",
                reply_markup=kb.buttons([[("Открыть задачу", f"task:{existing.task_id}")]]),
            )
            return
        client = (
            await ClientService(session).get_by_telegram_id(info.telegram_user_id)
            if info.telegram_user_id
            else None
        )
        token = uuid4().hex[:8]
        await state.update_data(
            doz_text=message.text or message.caption or "",
            doz_chat_id=message.chat.id,
            doz_message_id=message.message_id,
            doz_forwarded_user_id=info.telegram_user_id,
            doz_dedup_key=key,
            fwd_tg_id=info.telegram_user_id,
            fwd_name=info.display_name,
            fwd_username=info.username,
            fwd_token=token,
        )
        if client:
            await state.update_data(fwd_client_id=client.id)
            text = await _client_card_text(session, client)
            markup = kb.client_card(client.id, token)
        else:
            text = f"Отправитель: {escape(info.display_name)}.\nВыберите клиента или создайте карточку."
            markup = kb.buttons(
                [
                    [("➕ Создать клиента", f"fwd_new:{token}")],
                    [("👥 Выбрать клиента", f"fwd_pick:{token}:0")],
                    [("🏠 В меню", kb.MAIN)],
                ]
            )
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("fwd_new:"))
async def on_fwd_new_client(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    token = query.data.split(":")[1]
    if data.get("fwd_token") != token:
        await query.answer("Перешлите сообщение заново.", show_alert=True)
        return
    async with session_scope() as session:
        svc = ClientService(session)
        client = await svc.get(data["fwd_client_id"]) if data.get("fwd_client_id") else None
        if client is None and data.get("fwd_tg_id"):
            client = await svc.get_by_telegram_id(data["fwd_tg_id"])
        if client is None:
            client = await svc.create(
                data["fwd_name"],
                telegram_user_id=data.get("fwd_tg_id"),
                telegram_username=data.get("fwd_username"),
            )
        text = await _client_card_text(session, client)
    await state.update_data(fwd_client_id=client.id)
    await query.answer("Клиент выбран")
    await query.message.edit_text(text, reply_markup=kb.client_card(client.id, token))


@router.callback_query(F.data.startswith("fwd_pick:"))
async def on_fwd_pick(query: CallbackQuery, state: FSMContext) -> None:
    _, token, page = query.data.split(":")
    if (await state.get_data()).get("fwd_token") != token:
        await query.answer("Перешлите сообщение заново.", show_alert=True)
        return
    async with session_scope() as session:
        clients = await ClientService(session).list_active(limit=None)
    await query.answer()
    await query.message.edit_text(
        "Выберите клиента:",
        reply_markup=kb.paged(
            [(c.display_name, f"fwd_select:{token}:{c.id}") for c in clients],
            f"fwd_pick:{token}",
            int(page),
        ),
    )


@router.callback_query(F.data.startswith("fwd_select:"))
async def on_fwd_select(query: CallbackQuery, state: FSMContext) -> None:
    _, token, cid = query.data.split(":")
    if (await state.get_data()).get("fwd_token") != token:
        await query.answer("Перешлите сообщение заново.", show_alert=True)
        return
    async with session_scope() as session:
        client = await ClientService(session).get(int(cid))
        text = await _client_card_text(session, client)
    await state.update_data(fwd_client_id=client.id)
    await query.answer()
    await query.message.edit_text(text, reply_markup=kb.client_card(client.id, token))
