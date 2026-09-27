"""Common handlers: /start, main menu, and navigation back."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb

router = Router(name="common")


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer("Личный CRM-бот.\nЧто дальше?", reply_markup=kb.main_menu())


@router.callback_query(F.data == kb.MAIN)
async def on_main(query: CallbackQuery) -> None:
    await query.message.edit_text("Личный CRM-бот.\nЧто дальше?", reply_markup=kb.main_menu())
    await query.answer()


@router.callback_query(F.data == "noop")
async def on_noop(query: CallbackQuery) -> None:
    await query.answer()
