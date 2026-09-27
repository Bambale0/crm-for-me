"""Select positions across unpaid invoices and confirm their combined invoice."""

from decimal import Decimal
from html import escape
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app import keyboards as kb
from app.db import session_scope
from app.handlers.billing import STATUS_RU, _period_text
from app.services.billing_service import BillingService
from app.services.invoice_transfer_service import InvoiceTransferService
from app.states import InvoiceTransferFlow
from app.utils.money import format_money
from app.utils.time import month_label

router = Router(name="invoice_transfer")


async def _data(state: FSMContext, token: str) -> dict:
    data = await state.get_data()
    if data.get("transfer_token") != token:
        raise ValueError("Этот выбор устарел. Откройте объединение из нужного счёта заново.")
    return data


async def _selection(query: CallbackQuery, state: FSMContext, page: int = 0) -> None:
    data = await state.get_data()
    pid, token = data["transfer_target"], data["transfer_token"]
    async with session_scope() as session:
        candidates = await InvoiceTransferService(session).candidates(pid)
    chosen = set(data["transfer_items"])
    # Refresh the selection if a draft item disappeared before confirmation.
    chosen &= {item.id for item in candidates}
    await state.update_data(transfer_items=sorted(chosen))
    entries = [
        (
            f"{'✅' if i.id in chosen else '⬜'} #{i.billing_period_id} · {format_money(i.amount)} · {i.description[:30]}",
            f"transfer_pick:{token}:{i.id}:{page}",
        )
        for i in candidates
    ]
    extra = []
    if chosen:
        extra.append([("Продолжить", f"transfer_review:{token}")])
    extra.append([("Отмена", f"invoice:{pid}")])
    total = sum((i.amount for i in candidates if i.id in chosen), start=Decimal("0"))
    await query.message.edit_text(
        f"🧩 Добавить позиции к счёту #{pid}\n\n"
        + (
            "Отметьте позиции из других неоплаченных счетов этого клиента.\n"
            if candidates
            else "В других неоплаченных счетах этого клиента нет доступных позиций.\n"
        )
        + f"Выбрано: {len(chosen)} · {format_money(total)}",
        reply_markup=kb.paged(entries, f"transfer_page:{token}", page, extra),
    )


@router.callback_query(F.data.startswith("transfer_start:"))
async def on_start(query: CallbackQuery, state: FSMContext) -> None:
    pid = int(query.data.split(":")[1])
    async with session_scope() as session:
        await InvoiceTransferService(session).candidates(pid)
    await state.clear()
    await state.set_state(InvoiceTransferFlow.selecting)
    await state.update_data(
        transfer_target=pid,
        transfer_token=uuid4().hex[:12],
        transfer_key=uuid4().hex,
        transfer_items=[],
    )
    await query.answer()
    await _selection(query, state)


@router.callback_query(InvoiceTransferFlow.selecting, F.data.startswith("transfer_pick:"))
async def on_pick(query: CallbackQuery, state: FSMContext) -> None:
    _, token, item_id, page = query.data.split(":")
    data = await _data(state, token)
    selected = set(data["transfer_items"])
    item_id = int(item_id)
    selected.symmetric_difference_update({item_id})
    await state.update_data(transfer_items=sorted(selected))
    await query.answer()
    await _selection(query, state, int(page))


@router.callback_query(InvoiceTransferFlow.selecting, F.data.startswith("transfer_page:"))
async def on_page(query: CallbackQuery, state: FSMContext) -> None:
    _, token, page = query.data.split(":")
    await _data(state, token)
    await query.answer()
    await _selection(query, state, int(page))


@router.callback_query(F.data.startswith("transfer_back:"))
async def on_back(query: CallbackQuery, state: FSMContext) -> None:
    token = query.data.split(":")[1]
    await _data(state, token)
    await state.set_state(InvoiceTransferFlow.selecting)
    await query.answer()
    await _selection(query, state)


@router.callback_query(InvoiceTransferFlow.selecting, F.data.startswith("transfer_review:"))
async def on_review(query: CallbackQuery, state: FSMContext) -> None:
    token = query.data.split(":")[1]
    data = await _data(state, token)
    async with session_scope() as session:
        preview = await InvoiceTransferService(session).preview(
            data["transfer_target"], data["transfer_items"]
        )
        text = [f"Объединить выбранные позиции со счётом #{preview.target.id}?"]
        text.extend(
            f"• #{i.billing_period_id}: {escape(i.description[:180])} — {format_money(i.amount)}"
            for i in preview.selected[:8]
        )
        if len(preview.selected) > 8:
            text.append(f"И ещё {len(preview.selected) - 8} позиций.")
        text.extend(
            [
                f"\nИтого в общем счёте: {format_money(preview.total)}",
                f"Статус нового счёта: {STATUS_RU[preview.target.status]}.",
                f"Расчётный месяц: {month_label(preview.target.year, preview.target.month)}.",
                "Остальные позиции останутся в отдельных счетах. Старые номера сохранятся в истории как заменённые.",
                "Цены позиций этих счетов будут зафиксированы.",
            ]
        )
    await state.update_data(transfer_fingerprint=preview.fingerprint)
    await state.set_state(InvoiceTransferFlow.confirming)
    await query.answer()
    await query.message.edit_text(
        "\n".join(text),
        reply_markup=kb.buttons(
            [
                [("✅ Подтвердить объединение", f"transfer_confirm:{token}")],
                [
                    ("Изменить выбор", f"transfer_back:{token}"),
                    ("Отмена", f"invoice:{data['transfer_target']}"),
                ],
            ]
        ),
    )


@router.callback_query(InvoiceTransferFlow.confirming, F.data.startswith("transfer_confirm:"))
async def on_confirm(query: CallbackQuery, state: FSMContext) -> None:
    token = query.data.split(":")[1]
    data = await _data(state, token)
    async with session_scope() as session:
        result = await InvoiceTransferService(session).transfer(
            data["transfer_target"],
            data["transfer_items"],
            key=data["transfer_key"],
            expected=data["transfer_fingerprint"],
        )
        text, status, totals = await _period_text(session, result)
    # Clear after the committed result; repeated/stale callbacks cannot repeat the transfer.
    await state.clear()
    await query.answer("Позиции объединены")
    await query.message.edit_text(
        "✅ Позиции объединены.\n\n" + text,
        reply_markup=kb.billing_actions(
            result.id,
            status,
            result.client_id,
            invoice_total=totals.invoice_total,
            debt=totals.debt,
        ),
    )


@router.callback_query(F.data.startswith("invoice_links:"))
async def on_links(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    pid = int(query.data.split(":")[1])
    async with session_scope() as session:
        billing = BillingService(session)
        original = await billing.get_period(pid)
        replacements = await InvoiceTransferService(session).replacements(pid)
        entries = [(f"#{i.id} · {STATUS_RU[i.status]}", f"invoice:{i.id}") for i in replacements]
    await query.answer()
    await query.message.edit_text(
        f"Позиции счёта #{pid} учтены в счетах:",
        reply_markup=kb.paged(
            entries,
            "unused",
            extra=[[("⬅️ История", f"invoice_history:{original.client_id}:0")]],
        ),
    )
