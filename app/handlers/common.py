"""Recovery and main navigation always take precedence over FSM input."""

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app import keyboards as kb
from app.db import session_scope
from app.services.dashboard_service import DashboardService
from app.services.task_service import TaskService
from app.utils.money import format_money

router = Router(name="common")
WELCOME = "Перешлите сообщение клиента, чтобы создать дозаказ, или выберите раздел.\n/cancel — отменить ввод."


async def main_screen(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    async with session_scope() as session:
        tasks = await TaskService(session).list_active()
        if tasks:
            page = max(0, min(page, (len(tasks) - 1) // kb.PAGE_SIZE))
            lines = [f"🏠 <b>Главное меню</b>\n\n<b>Активные задачи: {len(tasks)}</b>"]
            for index, task in enumerate(
                tasks[page * kb.PAGE_SIZE : (page + 1) * kb.PAGE_SIZE],
                start=page * kb.PAGE_SIZE + 1,
            ):
                status = "🆕" if task.status == "NEW" else "⏳"
                context = escape(task.client.display_name[:60])
                if task.project:
                    context += " / " + escape(task.project.name[:60])
                lines.append(
                    f"{index}. {status} <b>{escape(task.title[:120])}</b>\n"
                    f"{context} · {format_money(task.amount, task.currency)}"
                )
            if len(tasks) > kb.PAGE_SIZE:
                lines.append(f"Страница {page + 1} из {(len(tasks) - 1) // kb.PAGE_SIZE + 1}")
            text = "\n\n".join(lines)
        else:
            stats = await DashboardService(session).overall_stats()
            text = (
                "🏠 <b>Главное меню</b>\n\nАктивных задач нет.\n\n"
                "💰 <b>Финансы за всё время</b>\nВсе счета, включая черновики.\n"
                f"Начислено: {format_money(stats.accrued)}\n"
                f"Выставлено: {format_money(stats.issued)}\n"
                f"Оплачено: {format_money(stats.paid)}\n"
                f"Долг: {format_money(stats.debt)}"
            )
    return f"{text}\n\n{WELCOME}", kb.main_menu(tasks, page)


@router.message(CommandStart())
@router.message(Command("cancel", "help", "menu"))
async def on_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, keyboard = await main_screen()
    await message.answer(text, reply_markup=keyboard)


@router.callback_query((F.data == kb.MAIN) | F.data.startswith(f"{kb.MAIN}:"))
async def on_main(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer()
    page = int(query.data.split(":")[1]) if ":" in query.data else 0
    text, keyboard = await main_screen(page)
    await query.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data == "noop")
async def on_noop(query: CallbackQuery) -> None:
    await query.answer()
