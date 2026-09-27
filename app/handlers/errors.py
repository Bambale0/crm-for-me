"""Recoverable failures never expose database queries or tracebacks to the owner."""

import logging
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app import keyboards as kb
from app.services.errors import (
    AlreadyExistsError,
    DomainError,
    InvalidTransitionError,
    InvoiceIssuedError,
    NotFoundError,
    OverpaymentError,
)

router = Router(name="errors")
logger = logging.getLogger("crm.errors")


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    exc = event.exception
    update = event.update
    callback = update.callback_query
    message = update.message or (callback.message if callback else None)
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in exc.message:
        return True
    messages = {
        AlreadyExistsError: "Такая запись уже существует. Откройте её через карточку клиента.",
        InvoiceIssuedError: "Счёт за этот месяц уже выставлен. Его суммы зафиксированы; действие не выполнено.",
        NotFoundError: "Запись не найдена. Откройте главное меню.",
        OverpaymentError: "Сумма превышает остаток долга. Введите меньшую сумму.",
        InvalidTransitionError: "Это действие недоступно в текущем статусе. Откройте карточку заново.",
    }
    if isinstance(exc, (DomainError, ValueError)):
        text = messages.get(
            type(exc), str(exc) if isinstance(exc, ValueError) else "Проверьте введённые данные."
        )
    else:
        # hide_parameters on the engine prevents SQL parameter dumps.
        logger.error(
            "Update failed update_id=%s error_type=%s",
            update.update_id,
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        text = (
            f"Не удалось выполнить действие. Попробуйте ещё раз или /start. Код: {update.update_id}"
        )
    if callback:
        await callback.answer(text[:190], show_alert=True)
    elif message:
        await message.answer(escape(text), reply_markup=kb.back_to_main())
    return True


@router.callback_query()
async def on_stale_callback(query: CallbackQuery) -> None:
    await query.answer("Эта кнопка устарела. Откройте /start.", show_alert=True)


@router.message()
async def on_unhandled_message(message: Message) -> None:
    await message.answer(
        "Перешлите сообщение клиента или выберите раздел. /cancel — отменить ввод.",
        reply_markup=kb.main_menu(),
    )
