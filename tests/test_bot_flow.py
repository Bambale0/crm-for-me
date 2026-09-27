"""Telegram transport is simulated; dispatcher, FSM, services and DB are real."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Message, Update
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db
from app.handlers import register_all_handlers
from app.middlewares.owner import OwnerOnlyMiddleware
from app.models.billing import BillingPeriod, Payment
from app.models.client import Client
from app.models.task import Task
from app.services.billing_service import BillingService

OWNER = 111


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=900,
                date=datetime.now(timezone.utc),
                chat={"id": OWNER, "type": "private"},
                text=method.text,
                reply_markup=method.reply_markup,
            )
        return True

    async def stream_content(self, url, **kwargs):
        yield b""


@pytest.fixture(scope="module")
def bot_dispatcher():
    transport = TelegramSession()
    bot = Bot(
        "123456:TEST_ONLY_NOT_A_REAL_TOKEN",
        session=transport,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dp.update.outer_middleware(OwnerOnlyMiddleware(OWNER))
    register_all_handlers(dp)
    return bot, dp, transport


@pytest_asyncio.fixture
async def harness(engine, monkeypatch, bot_dispatcher):
    bot, dp, transport = bot_dispatcher
    monkeypatch.setattr(
        app.db, "session_factory", async_sessionmaker(engine, expire_on_commit=False)
    )
    transport.calls.clear()

    class Harness:
        sequence = 100

        async def send(
            self, text=None, callback=None, origin=None, user=OWNER, chat_type="private"
        ):
            self.sequence += 1
            message = {
                "message_id": self.sequence,
                "date": datetime.now(timezone.utc),
                "chat": {"id": OWNER, "type": chat_type},
                "from": {"id": user, "is_bot": False, "first_name": "Owner"},
                "text": text or "Card",
            }
            if origin:
                message["forward_origin"] = origin
            payload = {"update_id": self.sequence}
            if callback:
                payload["callback_query"] = {
                    "id": str(self.sequence),
                    "from": message["from"],
                    "chat_instance": "test",
                    "message": message,
                    "data": callback,
                }
            else:
                payload["message"] = message
            await dp.feed_update(bot, Update.model_validate(payload))

        @property
        def last(self):
            return next(
                c
                for c in reversed(transport.calls)
                if isinstance(c, (SendMessage, EditMessageText))
            )

        def button(self, label):
            for row in self.last.reply_markup.inline_keyboard:
                for button in row:
                    if label in button.text:
                        return button.callback_data
            raise AssertionError(f"No button {label!r} in {self.last}")

        async def click(self, label):
            await self.send(callback=self.button(label))

    harness = Harness()
    await harness.send("/start")
    return harness


async def test_full_forward_to_paid_invoice(harness):
    h = harness
    await h.send(
        "Сделать <новую> форму",
        origin={
            "type": "user",
            "date": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "sender_user": {"id": 222, "is_bot": False, "first_name": "Client <name>"},
        },
    )
    await h.click("Создать клиента")
    assert "&lt;name&gt;" in h.last.text
    await h.click("Дозаказ")
    await h.click("Новый проект")
    await h.send("Сайт")
    await h.send("5000")
    await h.click("В работе")
    await h.click("Выполнено")
    async with app.db.session_factory() as s:
        task = await s.scalar(select(Task))
        tid, cid, pid, project_id = task.id, task.client_id, task.billing_period_id, task.project_id
        assert task.source.original_text == "Сделать <новую> форму"
    await h.send(callback=f"details:project:{project_id}")
    await h.click("Поле")
    await h.send("Домен")
    await h.send("example.test")
    await h.send(callback=f"recurring:{cid}:0")
    await h.click("Услуга")
    await h.send("Сервер")
    await h.send("5000")
    await h.send(datetime.now(timezone.utc).strftime("%Y-%m-01"))
    await h.send(callback=f"billing:{cid}")
    assert "10 000 ₽" in h.last.text
    await h.send(callback=f"billing:{cid}")
    await h.click("Выставить счёт")
    await h.click("Выставить")
    await h.click("Принять оплату")
    await h.send("4000")
    assert "Частично оплачен" in h.last.text and "6 000 ₽" in h.last.text
    await h.click("Принять оплату")
    await h.send("6000")
    assert "Статус: Оплачен" in h.last.text and "Долг: 0 ₽" in h.last.text
    async with app.db.session_factory.begin() as s:
        task = await s.get(Task, tid)
        task.amount = Decimal("9999")  # Historical snapshot independent of source edits.
    async with app.db.session_factory() as s:
        svc = BillingService(s)
        period = await svc.get_period(pid)
        assert (await svc.totals(period)).invoice_total == Decimal("10000")
        assert await s.scalar(select(func.count()).select_from(Payment)) == 2


async def test_hidden_sender_can_select_existing_and_manual_tasks_repeat(harness):
    h = harness
    await h.send(callback="client_new")
    await h.send("Existing")
    async with app.db.session_factory() as s:
        cid = (await s.scalar(select(Client))).id
    await h.send(
        "Hidden task",
        origin={
            "type": "hidden_user",
            "date": datetime.now(timezone.utc),
            "sender_user_name": "Hidden",
        },
    )
    await h.click("Выбрать клиента")
    await h.click("Existing")
    await h.click("Дозаказ")
    await h.click("Без проекта")
    await h.send("10")
    for title in ("Manual one", "Manual two"):
        await h.send(callback=f"doz_start:{cid}")
        await h.click("Без проекта")
        await h.send("20")
        await h.send(title)
    async with app.db.session_factory() as s:
        tasks = list((await s.scalars(select(Task))).all())
        clients = list((await s.scalars(select(Client))).all())
    assert (len(tasks), len(clients), clients[0].telegram_user_id) == (3, 1, None)


async def test_duplicate_forward_with_new_delivery_id_warns(harness):
    h = harness
    origin = {
        "type": "user",
        "date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "sender_user": {"id": 222, "is_bot": False, "first_name": "Client"},
    }
    await h.send("Work", origin=origin)
    await h.click("Создать клиента")
    await h.click("Дозаказ")
    await h.click("Без проекта")
    await h.send("5000")
    await h.send("Work", origin=origin)
    assert "уже создана задача" in h.last.text


async def test_cancel_clears_fsm_and_owner_group_is_rejected(harness, bot_dispatcher):
    h = harness
    _, _, transport = bot_dispatcher
    await h.send(callback="client_new")
    await h.send("/cancel")
    await h.send("Should not create")
    count = len(transport.calls)
    await h.send("/start", user=999)
    await h.send("/start", chat_type="group")
    async with app.db.session_factory() as s:
        clients = await s.scalar(select(func.count()).select_from(Client))
    assert (clients, len(transport.calls)) == (0, count)


async def test_project_notes_archive_search_and_recovery(harness):
    h = harness
    await h.send(callback="client_new")
    await h.send("Client")
    async with app.db.session_factory() as s:
        cid = (await s.scalar(select(Client))).id
    await h.send(callback=f"project_new:{cid}")
    await h.send("Archive me")
    await h.click("Данные и заметки")
    await h.click("Заметка")
    await h.send("Remember this")
    await h.click("Открыть данные")
    assert "Remember this" in h.last.text
    await h.click("В архив")
    confirm = h.button("Подтвердить")
    await h.send(callback=confirm)
    await h.send(callback=confirm)
    await h.send(callback="search")
    await h.send("Archive")
    await h.click("Archive me")
    assert "ARCHIVED" in h.last.text
    await h.click("Данные и заметки")
    await h.click("Вернуть из архива")
    await h.click("Подтвердить")


async def test_recurring_price_deactivate_and_history_navigation(harness):
    h = harness
    await h.send(callback="client_new")
    await h.send("Client")
    await h.click("Услуги")
    await h.click("Услуга")
    await h.send("Server")
    await h.send("3000")
    await h.send("2026-01-15")
    await h.click("Услуга")
    await h.click("Изменить цену")
    await h.send("5000")
    await h.click("Услуга")
    assert "5 000 ₽" in h.last.text
    await h.click("Отключить")
    await h.click("Отключить")
    await h.click("Услуги")
    await h.send("/start")
    await h.send(callback="dashboard")
    await h.click("По клиентам")


async def test_reminder_create_cancel_and_validation(harness):
    h = harness
    await h.send(callback="reminders:0")
    await h.click("Напоминание")
    await h.send("Позвонить клиенту")
    await h.send("bad date")
    assert "формате" in h.last.text
    await h.send("2099-12-01 12:00")
    await h.click("Напоминания")
    await h.click("Позвонить")
    await h.click("Отменить напоминание")
    await h.click("Напоминания")
    assert not any(
        "Позвонить" in button.text for row in h.last.reply_markup.inline_keyboard for button in row
    )


async def test_payment_error_preserves_input_and_no_partial_record(harness):
    from app.services.client_service import ClientService

    h = harness
    async with app.db.session_factory.begin() as s:
        cid = (await ClientService(s).create("Client")).id
        svc = BillingService(s)
        period = await svc.get_or_create_period(cid, 2026, 9)
        await svc.add_manual_item(period.id, "Work", Decimal("10000"))
        await svc.issue(period)
        pid = period.id
    await h.send(callback=f"pay:{pid}")
    await h.send("0")
    assert "больше 0" in h.last.text
    await h.send("10001")
    assert "превышает" in h.last.text
    await h.send("NaN")
    await h.send("4000")
    await h.click("Оплаты")
    assert "4 000 ₽" in h.last.text
    await h.click("Счёт")
    await h.click("Позиции")
    assert "Work" in h.last.text
    async with app.db.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Payment)) == 1


async def test_legacy_empty_invoice_has_no_payment_action(harness, bot_dispatcher):
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.methods import AnswerCallbackQuery

    from app.services.client_service import ClientService
    from app.utils.time import now_utc

    h = harness
    bot, dp, transport = bot_dispatcher
    async with app.db.session_factory.begin() as s:
        cid = (await ClientService(s).create("Client")).id
        invoice = BillingPeriod(
            client_id=cid, year=2026, month=9, status="ISSUED", issued_at=now_utc()
        )
        s.add(invoice)
        await s.flush()
        pid = invoice.id
    # Even an old payment button must never enter the payment FSM for debt=0.
    await h.send(callback=f"pay:{pid}")
    key = StorageKey(bot_id=bot.id, chat_id=OWNER, user_id=OWNER)
    assert await dp.storage.get_state(key) is None
    answers = [m for m in transport.calls if isinstance(m, AnswerCallbackQuery)]
    assert "Долга нет" in answers[-1].text
    await h.send(callback=f"invoice:{pid}")
    assert not any(
        "Принять оплату" in b.text for row in h.last.reply_markup.inline_keyboard for b in row
    )


async def test_on_demand_invoice_history_opens_exact_invoice(harness):
    from app.services.client_service import ClientService
    from app.utils.time import current_month

    h = harness
    async with app.db.session_factory.begin() as s:
        cid = (await ClientService(s).create("Client")).id
        svc = BillingService(s)
        first = await svc.get_or_create_period(cid, *current_month("Europe/Moscow"))
        await svc.add_manual_item(first.id, "First work", Decimal("19500"))
        await svc.issue(first)
        first_id = first.id
        second = await svc.get_or_create_period(cid, *current_month("Europe/Moscow"))
        await svc.add_manual_item(second.id, "Second work", Decimal("5000"))
        second_id = second.id
    await h.send(callback=f"billing:{cid}")
    await h.click("Выставить счёт")
    await h.click("Выставить")
    assert f"#{second_id}" in h.last.text
    await h.click("История счетов")
    await h.click(f"#{first_id} ")
    assert "19 500 ₽" in h.last.text
    await h.click("Принять оплату")
    await h.send("19500")
    assert f"#{first_id}" in h.last.text and "Долг: 0 ₽" in h.last.text
