"""Bot handlers package — routers are assembled in register_all_handlers()."""

from aiogram import Router

from app.handlers import billing, client, common, forward, task


def register_all_handlers(router: Router) -> None:
    router.include_router(common.router)
    router.include_router(forward.router)
    router.include_router(client.router)
    router.include_router(task.router)
    router.include_router(billing.router)
