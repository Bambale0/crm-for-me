"""Monthly services can be created, repriced and deactivated from a client card."""

from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.db import session_scope
from app.services.recurring_service import RecurringService
from app.states import RecurringFlow
from app.utils.money import format_money, to_decimal

router = Router(name="recurring")


@router.callback_query(F.data.startswith("recurring:"))
async def on_recurring(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, cid, page = query.data.split(":")
    async with session_scope() as session:
        charges = await RecurringService(session).list_for_client(int(cid), include_inactive=True)
    await query.answer()
    await query.message.edit_text(
        "Ежемесячные услуги:",
        reply_markup=kb.paged(
            [(("🔁 " if c.is_active else "⏹ ") + c.title, f"charge:{c.id}") for c in charges],
            f"recurring:{cid}",
            int(page),
            [[("➕ Услуга", f"charge_new:{cid}"), ("⬅️ Клиент", f"client:{cid}")]],
        ),
    )


@router.callback_query(F.data.startswith("charge:"))
async def on_charge(query: CallbackQuery) -> None:
    async with session_scope() as session:
        charge = await RecurringService(session).get(int(query.data.split(":")[1]))
    rows = [[("💰 Изменить цену", f"charge_price:{charge.id}")]] if charge.is_active else []
    if charge.is_active:
        rows.append([("⏹ Отключить", f"charge_stop_ask:{charge.id}")])
    rows.append([("⬅️ Услуги", f"recurring:{charge.client_id}:0")])
    await query.answer()
    await query.message.edit_text(
        f"🔁 <b>{escape(charge.title)}</b>\n{format_money(charge.amount)} / месяц\nС {charge.active_from:%d.%m.%Y}\n"
        + ("Активна" if charge.is_active else "Отключена"),
        reply_markup=kb.buttons(rows),
    )


@router.callback_query(F.data.startswith("charge_new:"))
async def on_charge_new(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(charge_client_id=int(query.data.split(":")[1]))
    await state.set_state(RecurringFlow.title)
    await query.answer()
    await query.message.edit_text("Название ежемесячной услуги:", reply_markup=kb.back_to_main())


@router.message(RecurringFlow.title)
async def on_charge_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not 1 <= len(title) <= 255:
        raise ValueError("Название: от 1 до 255 символов")
    await state.update_data(charge_title=title)
    await state.set_state(RecurringFlow.amount)
    await message.answer("Стоимость за месяц:", reply_markup=kb.back_to_main())


@router.callback_query(F.data.startswith("charge_price:"))
async def on_charge_price(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(charge_edit_id=int(query.data.split(":")[1]))
    await state.set_state(RecurringFlow.amount)
    await query.answer()
    await query.message.edit_text("Новая стоимость за месяц:", reply_markup=kb.back_to_main())


@router.message(RecurringFlow.amount)
async def on_charge_amount(message: Message, state: FSMContext) -> None:
    amount = to_decimal(message.text or "")
    data = await state.get_data()
    if data.get("charge_edit_id"):
        async with session_scope() as session:
            svc = RecurringService(session)
            charge = await svc.get(data["charge_edit_id"])
            await svc.update_amount(charge, amount)
        await state.clear()
        await message.answer(
            "Цена сохранена. Выставленные счета сохранены без изменений.",
            reply_markup=kb.buttons([[("Услуга", f"charge:{charge.id}")]]),
        )
        return
    await state.update_data(charge_amount=str(amount))
    await state.set_state(RecurringFlow.start)
    await message.answer(
        "Дата начала в формате ГГГГ-ММ-ДД. Начальный месяц начисляется полностью.",
        reply_markup=kb.back_to_main(),
    )


@router.message(RecurringFlow.start)
async def on_charge_start(message: Message, state: FSMContext) -> None:
    try:
        start = date.fromisoformat(message.text or "")
    except ValueError:
        raise ValueError("Введите дату в формате ГГГГ-ММ-ДД") from None
    data = await state.get_data()
    async with session_scope() as session:
        charge = await RecurringService(session).create(
            data["charge_client_id"], data["charge_title"], data["charge_amount"], active_from=start
        )
    await state.clear()
    await message.answer(
        "Услуга добавлена.", reply_markup=kb.buttons([[("Услуга", f"charge:{charge.id}")]])
    )


@router.callback_query(F.data.startswith("charge_stop_ask:"))
async def on_charge_stop_ask(query: CallbackQuery) -> None:
    cid = int(query.data.split(":")[1])
    await query.answer()
    await query.message.edit_text(
        "Отключить услугу? Она будет убрана из черновиков. Выставленные счета сохранятся.",
        reply_markup=kb.buttons(
            [[("Отключить", f"charge_stop:{cid}"), ("Назад", f"charge:{cid}")]]
        ),
    )


@router.callback_query(F.data.startswith("charge_stop:"))
async def on_charge_stop(query: CallbackQuery) -> None:
    async with session_scope() as session:
        svc = RecurringService(session)
        charge = await svc.get(int(query.data.split(":")[1]))
        await svc.deactivate(charge)
    await query.answer("Услуга отключена")
    await query.message.edit_text(
        "Услуга отключена.",
        reply_markup=kb.buttons([[("Услуги", f"recurring:{charge.client_id}:0")]]),
    )
