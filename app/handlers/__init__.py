"""Router assembly, with fallback handlers last."""

from aiogram import Router

from app.handlers import (
    billing,
    client,
    common,
    details,
    errors,
    forward,
    invoice_transfer,
    recurring,
    reminder,
    task,
)


def register_all_handlers(router: Router) -> None:
    router.include_routers(
        common.router,
        forward.router,
        client.router,
        task.router,
        details.router,
        recurring.router,
        reminder.router,
        billing.router,
        invoice_transfer.router,
        errors.router,
    )
