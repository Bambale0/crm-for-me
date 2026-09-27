"""Recovery and main navigation always take precedence over FSM input."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb

router = Router(name="common")
WELCOME = "Личный CRM-бот.\nПерешлите сообщение клиента, чтобы создать дозаказ, или выберите раздел.\n/cancel — отменить ввод."


@router.message(CommandStart())
@router.message(Command("cancel", "help"))
async def on_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=kb.main_menu())


@router.callback_query(F.data == kb.MAIN)
async def on_main(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer()
    await query.message.edit_text(WELCOME, reply_markup=kb.main_menu())


@router.callback_query(F.data == "noop")
async def on_noop(query: CallbackQuery) -> None:
    await query.answer()
