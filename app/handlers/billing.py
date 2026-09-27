"""Billing, payment, dashboard, month navigation and search handlers."""

from __future__ import annotations

from html import escape
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.services.billing_service import BillingService, Totals
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService
from app.services.errors import InvalidAmountError
from app.services.payment_service import PaymentService
from app.services.search_service import SearchService
from app.states import PaymentFlow, SearchFlow
from app.utils.money import format_money, to_decimal
from app.utils.time import format_local, month_label

router = Router(name="billing")

STATUS_RU = {
    "SUPERSEDED": "Заменён при объединении",
    "DRAFT": "Черновик",
    "ISSUED": "Выставлен",
    "PARTIALLY_PAID": "Частично оплачен",
    "PAID": "Оплачен",
    "CANCELLED": "Отменён",
}


async def _period_text(session, period) -> tuple[str, str, Totals]:
    svc = BillingService(session)
    client = await ClientService(session).get(period.client_id)
    totals = await svc.totals(period)
    lines = [
        f"🧾 <b>Счёт #{period.id}</b> — {escape(client.display_name)}",
        f"Создан: {format_local(period.created_at, get_settings().timezone)}",
        f"Статус: {STATUS_RU.get(period.status, period.status)}",
    ]
    items = await svc.items(period)
    for item in items[:8]:
        lines.append(f"• {escape(item.description[:180])} — {format_money(item.amount, 'RUB')}")
    if len(items) > 8:
        lines.append(f"Ещё позиций: {len(items) - 8}. Откройте список позиций ниже.")
    lines.append("")
    lines.append(f"Итого: {format_money(totals.invoice_total, 'RUB')}")
    lines.append(f"Оплачено: {format_money(totals.paid_total, 'RUB')}")
    lines.append(f"Долг: {format_money(totals.debt, 'RUB')}")
    if period.status == "SUPERSEDED":
        lines.append(
            "\nИсторический счёт. Его позиции учтены в новых счетах; этот номер оплачивать не нужно."
        )
    elif totals.invoice_total <= 0 and period.status == "DRAFT":
        lines.append(
            "\nПока нет суммы к оплате. Отметьте задачу «Выполнено» или добавьте услугу — затем выставьте счёт в любой момент."
        )
    elif totals.debt <= 0:
        lines.append("\nДолга нет — оплату вводить не нужно.")
    return "\n".join(lines), period.status, totals


@router.callback_query(F.data.startswith(f"{kb.BILLING}:"))
async def on_billing(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = query.data.split(":")
    client_id = int(parts[1])
    async with session_scope() as session:
        svc = BillingService(session)
        period = (
            await svc.get_or_create_period(client_id, int(parts[2]), int(parts[3]))
            if len(parts) > 3
            else await svc.current_period(client_id, get_settings().timezone)
        )
        await svc.reconcile_draft(period)
        text, status, totals = await _period_text(session, period)
    await query.message.edit_text(
        text,
        reply_markup=kb.billing_actions(
            period.id,
            status,
            period.client_id,
            invoice_total=totals.invoice_total,
            debt=totals.debt,
        ),
    )
    await query.answer()


@router.callback_query(F.data.startswith("invoice:"))
async def on_invoice(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        svc = BillingService(session)
        period = await svc.get_period(int(query.data.split(":")[1]))
        await svc.reconcile_draft(period)
        text, status, totals = await _period_text(session, period)
    await query.answer()
    await query.message.edit_text(
        text,
        reply_markup=kb.billing_actions(
            period.id,
            status,
            period.client_id,
            invoice_total=totals.invoice_total,
            debt=totals.debt,
        ),
    )


@router.callback_query(F.data.startswith("invoice_history:"))
async def on_invoice_history(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, cid, page = query.data.split(":")
    async with session_scope() as session:
        svc = BillingService(session)
        invoices = await svc.list_for_client(int(cid))
        entries = []
        for invoice in invoices:
            await svc.reconcile_draft(invoice)
            totals = await svc.totals(invoice)
            entries.append(
                (
                    f"#{invoice.id} {format_money(totals.invoice_total)} · {STATUS_RU[invoice.status]}",
                    f"invoice:{invoice.id}",
                )
            )
    await query.answer()
    await query.message.edit_text(
        "История счетов:" if invoices else "Счетов пока нет.",
        reply_markup=kb.paged(
            entries,
            f"invoice_history:{cid}",
            int(page),
            [[("➕ Новый счёт", f"billing:{cid}"), ("⬅️ Клиент", f"client:{cid}")]],
        ),
    )


@router.callback_query(F.data.startswith(f"{kb.BILLING_ISSUE}:"))
async def on_billing_issue(query: CallbackQuery) -> None:
    period_id = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = BillingService(session)
        period = await svc.get_period(period_id)
        await svc.issue(period)
        text, status, totals = await _period_text(session, period)
    await query.message.edit_text(
        text,
        reply_markup=kb.billing_actions(
            period.id,
            status,
            period.client_id,
            invoice_total=totals.invoice_total,
            debt=totals.debt,
        ),
    )
    await query.answer("Счёт выставлен")


@router.callback_query(F.data.startswith("billing_issue_ask:"))
async def on_issue_ask(query: CallbackQuery) -> None:
    pid = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = BillingService(session)
        period = await svc.get_period(pid)
        await svc.reconcile_draft(period)
        totals = await svc.totals(period)
    if totals.invoice_total <= 0:
        raise InvalidAmountError(
            "Нельзя выставить счёт на 0 ₽. Сначала отметьте задачу «Выполнено» или добавьте услугу."
        )
    await query.answer()
    await query.message.edit_text(
        f"Выставить счёт на {format_money(totals.invoice_total)}? Позиции и цены будут зафиксированы.",
        reply_markup=kb.buttons(
            [
                [
                    ("Выставить", f"billing_issue:{pid}"),
                    ("Назад", f"invoice:{period.id}"),
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith(f"{kb.PAY}:"))
async def on_pay(query: CallbackQuery, state: FSMContext) -> None:
    period_id = int(query.data.split(":")[1])
    async with session_scope() as session:
        svc = BillingService(session)
        period = await svc.get_period(period_id)
        totals = await svc.ensure_payable(period)
    await state.clear()
    await state.update_data(pay_period_id=period_id, pay_key=uuid4().hex)
    await state.set_state(PaymentFlow.waiting_amount)
    await query.answer()
    await query.message.edit_text(
        f"Долг: {format_money(totals.debt)}. Введите сумму оплаты:", reply_markup=kb.back_to_main()
    )


@router.message(PaymentFlow.waiting_amount)
async def on_pay_amount(message: Message, state: FSMContext) -> None:
    amount = to_decimal(message.text or "")
    data = await state.get_data()
    period_id = data["pay_period_id"]
    async with session_scope() as session:
        svc = BillingService(session)
        await PaymentService(session).add_payment(
            period_id, amount, idempotency_key=data["pay_key"]
        )
        period = await svc.get_period(period_id)
        text, status, totals = await _period_text(session, period)
    await state.clear()
    await message.answer(
        f"✅ Оплата {format_money(amount)} зачтена.\n\n" + text,
        reply_markup=kb.billing_actions(
            period.id,
            status,
            period.client_id,
            invoice_total=totals.invoice_total,
            debt=totals.debt,
        ),
    )


@router.callback_query(F.data.startswith("invoice_items:") | F.data.startswith("payments:"))
async def on_financial_list(query: CallbackQuery) -> None:
    kind, pid, page = query.data.split(":")
    page = max(0, int(page))
    async with session_scope() as session:
        svc = BillingService(session)
        period = await svc.get_period(int(pid))
        if kind == "invoice_items":
            rows = [
                f"{escape(i.description[:600])} — {format_money(i.amount)}"
                for i in await svc.items(period)
            ]
        else:
            from app.utils.time import format_local

            rows = [
                f"{format_local(p.paid_at, get_settings().timezone)} — {format_money(p.amount)}"
                for p in await svc.payments(period)
            ]
    text = "\n\n".join(rows[page * 4 : (page + 1) * 4]) or "Пока пусто."
    nav = []
    if page:
        nav.append(("⬅️", f"{kind}:{pid}:{page - 1}"))
    if (page + 1) * 4 < len(rows):
        nav.append(("➡️", f"{kind}:{pid}:{page + 1}"))
    buttons = [nav] if nav else []
    if kind == "invoice_items" and period.status in ("DRAFT", "ISSUED"):
        buttons.append([("🧩 Объединить позиции", f"transfer_start:{period.id}")])
    buttons.append([("⬅️ Счёт", f"invoice:{period.id}")])
    await query.answer()
    await query.message.edit_text(text, reply_markup=kb.buttons(buttons))


# ── dashboard / month ─────────────────────────────────────────────────────
@router.callback_query(F.data == "dashboard")
async def on_dashboard(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
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

    entries = (
        [(f"👤 {c.display_name}", f"client:{c.id}") for c in result.clients]
        + [(f"📁 {p.name}", f"project:{p.id}") for p in result.projects]
        + [(f"📌 {t.title}", f"task:{t.id}") for t in result.tasks]
    )
    await state.update_data(search_entries=entries)
    await message.answer(
        "Результаты поиска:" if entries else "Ничего не найдено.",
        reply_markup=kb.paged(entries, "search_page"),
    )


@router.callback_query(F.data.startswith("search_page:"))
async def on_search_page(query: CallbackQuery, state: FSMContext) -> None:
    entries = (await state.get_data()).get("search_entries", [])
    await query.answer()
    await query.message.edit_text(
        "Результаты поиска:" if entries else "Повторите поиск.",
        reply_markup=kb.paged(entries, "search_page", int(query.data.split(":")[1])),
    )


@router.callback_query(F.data.startswith("month_clients:"))
async def on_month_clients(query: CallbackQuery) -> None:
    _, year, month, page = query.data.split(":")
    year, month, page = int(year), int(month), int(page)
    async with session_scope() as session:
        billing = BillingService(session)
        await billing.generate_month(year, month)
        periods = await billing.list_for_month(year, month)
        entries = []
        for period in periods:
            await billing.reconcile_draft(period)
            client = await ClientService(session).get(period.client_id)
            totals = await billing.totals(period)
            entries.append(
                (
                    f"#{period.id} {STATUS_RU[period.status]} · {client.display_name}: {format_money(totals.invoice_total)} / долг {format_money(totals.debt)}",
                    f"invoice:{period.id}",
                )
            )
    await query.answer()
    await query.message.edit_text(
        f"{month_label(year, month)} — счета по клиентам:",
        reply_markup=kb.paged(entries, f"month_clients:{year}:{month}", page),
    )
