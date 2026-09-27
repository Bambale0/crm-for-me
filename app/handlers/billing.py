"""Billing, payment, dashboard, month navigation and search handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.services.billing_service import BillingService
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.payment_service import PaymentService
from app.services.search_service import SearchService
from app.states import PaymentFlow, SearchFlow
from app.utils.money import format_money, to_decimal
from app.utils.time import month_label

router = Router(name="billing")

STATUS_RU = {
    "DRAFT": "Черновик",
    "ISSUED": "Выставлен",
    "PARTIALLY_PAID": "Частично оплачен",
    "PAID": "Оплачен",
    "CANCELLED": "Отменён",
}


async def _period_text(session, period) -> tuple[str, str]:
    svc = BillingService(session)
    client = await ClientService(session).get(period.client_id)
    totals = await svc.totals(period)
    lines = [
        f"🧾 <b>{client.display_name}</b> — {month_label(period.year, period.month)}",
        f"Статус: {STATUS_RU.get(period.status, period.status)}",
    ]
    for item in await svc.items(period):
        lines.append(f"• {item.description} — {format_money(item.amount, 'RUB')}")
    lines.append("")
    lines.append(f"Итого: {format_money(totals.invoice_total, 'RUB')}")
    lines.append(f"Оплачено: {format_money(totals.paid_total, 'RUB')}")
    lines.append(f"Долг: {format_money(totals.debt, 'RUB')}")
    return "\n".join(lines), period.status


@router.callback_query(F.data.startswith(f"{kb.BILLING}:"))
async def on_billing(query: CallbackQuery) -> None:
    client_id = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = BillingService(session)
        period = await svc.current_period(client_id, get_settings().timezone)
        await svc.reconcile_draft(period)
        text, status = await _period_text(session, period)
    await query.message.edit_text(text, reply_markup=kb.billing_actions(period.id, status))
    await query.answer()


@router.callback_query(F.data.startswith(f"{kb.BILLING_ISSUE}:"))
async def on_billing_issue(query: CallbackQuery) -> None:
    period_id = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = BillingService(session)
        period = await svc.get_period(period_id)
        await svc.issue(period)
        text, status = await _period_text(session, period)
    await query.message.edit_text(text, reply_markup=kb.billing_actions(period.id, status))
    await query.answer("Счёт выставлен")


@router.callback_query(F.data.startswith(f"{kb.PAY}:"))
async def on_pay(query: CallbackQuery, state: FSMContext) -> None:
    period_id = int(query.data.split(":")[1])
    await state.update_data(pay_period_id=period_id)
    await state.set_state(PaymentFlow.waiting_amount)
    await query.message.edit_text("Введите сумму оплаты:")
    await query.answer()


@router.message(PaymentFlow.waiting_amount)
async def on_pay_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = to_decimal(message.text or "")
    except Exception:
        await message.answer("Не удалось распознать сумму. Введите число, например: 5000")
        return

    data = await state.get_data()
    period_id = data["pay_period_id"]
    async with session_scope() as session:
        svc = BillingService(session)
        payments = PaymentService(session)
        try:
            await payments.add_payment(period_id, amount)
        except Exception as exc:
            await state.clear()
            await message.answer(f"Оплата не принята: {exc}")
            return
        period = await svc.get_period(period_id)
        text, status = await _period_text(session, period)

    await state.clear()
    await message.answer(
        f"✅ Оплата {format_money(amount, 'RUB')} зачтена.",
        reply_markup=kb.billing_actions(period_id, status),
    )
    await message.answer(text)


# ── dashboard / month ─────────────────────────────────────────────────────
@router.callback_query(F.data == "dashboard")
async def on_dashboard(query: CallbackQuery) -> None:
    tz = get_settings().timezone
    year, month = _month_from_tz(tz)
    await _show_month(query, year, month)


def _month_from_tz(tz: str) -> tuple[int, int]:
    from app.utils.time import current_month

    return current_month(tz)


async def _show_month(query: CallbackQuery, year: int, month: int) -> None:
    tz = get_settings().timezone
    async with session_scope() as session:
        dash = DashboardService(session, tz_name=tz)
        stats = await dash.month_stats(year, month)
    text = (
        f"📊 <b>{month_label(year, month)}</b>\n"
        f"Начислено: {format_money(stats.accrued, 'RUB')}\n"
        f"Выставлено: {format_money(stats.issued, 'RUB')}\n"
        f"Оплачено: {format_money(stats.paid, 'RUB')}\n"
        f"Долг: {format_money(stats.debt, 'RUB')}"
    )
    await query.message.edit_text(text, reply_markup=kb.month_nav(year, month))
    await query.answer()


@router.callback_query(F.data.startswith(f"{kb.MONTH}:"))
async def on_month_nav(query: CallbackQuery) -> None:
    parts = query.data.split(":")
    year = int(parts[1])
    month = int(parts[2])
    delta = int(parts[3])
    month += delta
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    await _show_month(query, year, month)


# ── search ────────────────────────────────────────────────────────────────
@router.callback_query(F.data == kb.SEARCH)
async def on_search(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchFlow.waiting_query)
    await query.message.edit_text("Введите поисковый запрос:")
    await query.answer()


@router.message(SearchFlow.waiting_query)
async def on_search_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Запрос не может быть пустым.")
        return
    await state.clear()
    async with session_scope() as session:
        result = await SearchService(session).search(query)

    lines = [f"Результаты по «{query}»:"]
    for c in result.clients:
        lines.append(f"👤 {c.display_name}")
    for p in result.projects:
        lines.append(f"📁 {p.name}")
    for t in result.tasks:
        lines.append(f"📌 {t.title}")
    if len(lines) == 1:
        lines.append("Ничего не найдено.")
    await message.answer("\n".join(lines), reply_markup=kb.main_menu())


