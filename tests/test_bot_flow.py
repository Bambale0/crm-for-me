"""Telegram transport is simulated; dispatcher, FSM, services and DB are real."""

from datetime import datetime, timedelta, timezone
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
            self,
            text=None,
            callback=None,
            origin=None,
            user=OWNER,
            chat_type="private",
            received_at=None,
        ):
            self.sequence += 1
            message = {
                "message_id": self.sequence,
                "date": received_at or datetime.now(timezone.utc),
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


async def test_select_positions_from_other_invoice_confirm_and_pay(harness):
    from app.services.client_service import ClientService

    h = harness
    async with app.db.session_factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
        billing = BillingService(session)
        source = await billing.get_or_create_period(cid, 2026, 9)
        await billing.add_manual_item(source.id, "Move <work>", Decimal("19500"))
        await billing.add_manual_item(source.id, "Keep", Decimal("5000"))
        await billing.issue(source)
        target = await billing.get_or_create_period(cid, 2026, 9)
        await billing.add_manual_item(target.id, "Target", Decimal("1000"))
        await billing.issue(target)
        source_id, target_id = source.id, target.id
    await h.send(callback=f"invoice:{target_id}")
    await h.click("Позиции")
    await h.click("Объединить позиции")
    assert "Выбрано: 0" in h.last.text
    await h.click("Move <work>")
    await h.click("Продолжить")
    assert "20 500 ₽" in h.last.text and "&lt;work&gt;" in h.last.text
    await h.click("Изменить выбор")
    await h.click("Продолжить")
    confirm = h.button("Подтвердить объединение")
    await h.click("Подтвердить объединение")
    assert "Позиции объединены" in h.last.text and "20 500 ₽" in h.last.text
    await h.send(callback=confirm)
    await h.click("Принять оплату")
    await h.send("20500")
    assert "Долг: 0 ₽" in h.last.text
    await h.send(callback=f"invoice:{source_id}")
    assert "Заменён" in h.last.text
    assert "Принять оплату" not in str(h.last.reply_markup)
    await h.click("Новые счета")
    async with app.db.session_factory() as session:
        from app.services.dashboard_service import DashboardService

        assert await DashboardService(session).total_debt(cid) == Decimal("5000")


async def test_transfer_selection_pagination_and_cancel(harness):
    from app.services.client_service import ClientService

    h = harness
    async with app.db.session_factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
        billing = BillingService(session)
        source = await billing.get_or_create_period(cid, 2026, 9)
        for index in range(7):
            await billing.add_manual_item(source.id, f"Position {index}", Decimal("1000"))
        await billing.issue(source)
        target = await billing.get_or_create_period(cid, 2026, 9)
        target_id = target.id
    await h.send(callback=f"transfer_start:{target_id}")
    await h.click("Position 0")
    await h.click("➡️")
    await h.click("Position 6")
    assert "Выбрано: 2" in h.last.text
    await h.click("⬅️")
    assert "✅" in next(
        b.text for row in h.last.reply_markup.inline_keyboard for b in row if "Position 0" in b.text
    )
    await h.click("Position 0")
    assert "Выбрано: 1" in h.last.text
    await h.click("Отмена")
    async with app.db.session_factory() as session:
        assert (await BillingService(session).get_period(source.id)).status == "ISSUED"


async def test_task_list_accepts_typed_title_in_current_project(harness):
    from app.services.client_service import ClientService
    from app.services.project_service import ProjectService

    h = harness
    async with app.db.session_factory.begin() as session:
        await ClientService(session).create("Other client")
        cid = (await ClientService(session).create("Client")).id
        project = await ProjectService(session).create(cid, "Website")
        pid = project.id
    await h.send(callback=f"tasks:{pid}:project")
    assert "Задач пока нет" in h.last.text
    await h.send("перекинуть домен")
    assert "Введите сумму" in h.last.text
    await h.send("1500")
    assert h.button("Главное меню") == "main"
    async with app.db.session_factory() as session:
        task = await session.scalar(select(Task))
        assert (task.title, task.client_id, task.project_id, task.amount) == (
            "перекинуть домен",
            cid,
            pid,
            Decimal("1500"),
        )
    await h.click("Задачи")
    assert "перекинуть домен" in str(h.last.reply_markup)


async def test_add_task_button_in_client_list_validates_input(harness):
    from app.services.client_service import ClientService

    h = harness
    async with app.db.session_factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
    await h.send(callback=f"tasks:{cid}")
    await h.click("Добавить задачу")
    assert "Введите название" in h.last.text
    await h.send("x" * 501)
    assert "500" in h.last.text
    await h.send("Проверить <домен>")
    assert "&lt;домен&gt;" in h.last.text
    await h.send("не знаю")
    async with app.db.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Task)) == 0
    await h.send("0")
    async with app.db.session_factory() as session:
        task = await session.scalar(select(Task))
        assert task.title == "Проверить <домен>" and task.project_id is None
        assert task.amount == 0


async def test_leaving_task_list_clears_quick_creation_context(harness):
    from app.services.client_service import ClientService

    h = harness
    async with app.db.session_factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
    await h.send(callback=f"tasks:{cid}")
    await h.click("Назад")
    await h.send("Случайный текст")
    await h.send(callback=f"tasks:{cid}")
    await h.send("Отменяемая задача")
    await h.send("/cancel")
    await h.send("5000")
    async with app.db.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Task)) == 0


async def test_quick_task_refuses_project_archived_during_entry(harness):
    from app.services.client_service import ClientService
    from app.services.project_service import ProjectService

    h = harness
    async with app.db.session_factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
        pid = (await ProjectService(session).create(cid, "Website")).id
    await h.send(callback=f"task_new:{pid}:project")
    await h.send("перекинуть домен")
    async with app.db.session_factory.begin() as session:
        await ProjectService(session).set_archived(pid, True)
    await h.send("1500")
    assert "архивирован" in h.last.text
    await h.send(callback=f"tasks:{pid}:project")
    assert "Добавить задачу" not in str(h.last.reply_markup)
    async with app.db.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Task)) == 0


async def test_quick_task_from_paginated_list_keeps_project(harness):
    from app.services.client_service import ClientService
    from app.services.project_service import ProjectService
    from app.services.task_service import TaskService

    h = harness
    async with app.db.session_factory.begin() as session:
        cid = (await ClientService(session).create("Client")).id
        pid = (await ProjectService(session).create(cid, "Website")).id
        for index in range(6):
            await TaskService(session).create(cid, f"Existing {index}", project_id=pid)
    await h.send(callback=f"tasks:{pid}:project")
    await h.click("➡️")
    await h.click("Добавить задачу")
    await h.send("New task")
    await h.send("100")
    async with app.db.session_factory() as session:
        task = await session.scalar(select(Task).where(Task.title == "New task"))
        assert task.project_id == pid and task.client_id == cid


async def test_home_lists_only_active_tasks_and_returns_from_card(harness):
    from app.models.enums import TaskStatus
    from app.services.client_service import ClientService
    from app.services.project_service import ProjectService
    from app.services.task_service import TaskService

    h = harness
    async with app.db.session_factory.begin() as s:
        client = await ClientService(s).create("Клиент <один>")
        project = await ProjectService(s).create(client.id, "Сайт & бот")
        svc = TaskService(s)
        active = await svc.create(client.id, "Перенести <домен>", project_id=project.id)
        working = await svc.create(client.id, "Опубликовать пост", amount="1500")
        await svc.set_status(working, TaskStatus.IN_PROGRESS)
        done = await svc.create(client.id, "Уже готово")
        await svc.set_status(done, TaskStatus.DONE)
        cancelled = await svc.create(client.id, "Уже отменено")
        await svc.set_status(cancelled, TaskStatus.CANCELLED)
        tid = active.id
    await h.send(callback=f"task:{tid}")
    await h.click("Главное меню")
    assert "Активные задачи: 2" in h.last.text
    assert "Перенести &lt;домен&gt;" in h.last.text
    assert "Клиент &lt;один&gt;" in h.last.text and "Сайт &amp; бот" in h.last.text
    assert "Опубликовать пост" in h.last.text and "1 500 ₽" in h.last.text
    assert "Уже готово" not in h.last.text and "Уже отменено" not in h.last.text
    await h.click("Перенести")
    assert "Статус: Новая" in h.last.text
    await h.click("Название")
    await h.send(callback="main")
    await h.send("Не менять название")
    assert "Активные задачи: 2" in h.last.text
    async with app.db.session_factory() as s:
        assert (await s.get(Task, tid)).title == "Перенести <домен>"


async def test_home_pagination_and_empty_page_after_tasks_finish(harness):
    from app.models.enums import TaskStatus
    from app.services.client_service import ClientService
    from app.services.task_service import TaskService

    h = harness
    async with app.db.session_factory.begin() as s:
        client = await ClientService(s).create("Клиент")
        svc = TaskService(s)
        for number in range(7):
            await svc.create(client.id, f"Задание {number}")
    await h.send("/start")
    assert "Активные задачи: 7" in h.last.text
    assert (
        sum(
            b.callback_data.startswith("task:")
            for r in h.last.reply_markup.inline_keyboard
            for b in r
        )
        == 5
    )
    await h.click("➡️")
    assert (
        sum(
            b.callback_data.startswith("task:")
            for r in h.last.reply_markup.inline_keyboard
            for b in r
        )
        == 2
    )
    assert h.button("Клиенты") == "clients"
    async with app.db.session_factory.begin() as s:
        svc = TaskService(s)
        for task in await svc.list_all():
            await svc.set_status(task, TaskStatus.CANCELLED)
    await h.send(callback="main:1")
    assert "Активных задач нет" in h.last.text
    assert "за всё время" in h.last.text
    assert "Долг: 0 ₽" in h.last.text


async def test_empty_home_money_totals_across_months_without_double_count(harness):
    from app.models.enums import BillingStatus
    from app.services.client_service import ClientService
    from app.services.payment_service import PaymentService

    h = harness
    async with app.db.session_factory.begin() as s:
        client = await ClientService(s).create("Клиент")
        billing = BillingService(s)
        for month, amount, payment in [(1, "1000.50", "300.25"), (2, "2000", "2000")]:
            invoice = await billing.get_or_create_period(client.id, 2025, month)
            await billing.add_manual_item(invoice.id, "Работа", Decimal(amount))
            await billing.issue(invoice)
            await PaymentService(s).add_payment(invoice.id, payment)
        draft = await billing.get_or_create_period(client.id, 2026, 1)
        await billing.add_manual_item(draft.id, "Черновик", Decimal("400"))
        for month, status in [(2, BillingStatus.CANCELLED), (3, BillingStatus.SUPERSEDED)]:
            ignored = await billing.get_or_create_period(client.id, 2026, month)
            await billing.add_manual_item(ignored.id, "История", Decimal("9000"))
            ignored.status = status.value
        before = await s.scalar(select(func.count()).select_from(BillingPeriod))
    for entry in ["/start", "/cancel", "/menu", "/help"]:
        await h.send(entry)
        assert "Активных задач нет" in h.last.text
        assert "Начислено: 3 400.50 ₽" in h.last.text
        assert "Выставлено: 3 000.50 ₽" in h.last.text
        assert "Оплачено: 2 300.25 ₽" in h.last.text
        assert "Долг: 1 100.25 ₽" in h.last.text
    async with app.db.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(BillingPeriod)) == before


@pytest.mark.parametrize("project_count", [0, 1, 2])
async def test_known_forward_saves_task_and_selects_only_project(harness, project_count):
    from app.services.client_service import ClientService
    from app.services.project_service import ProjectService

    h = harness
    async with app.db.session_factory.begin() as s:
        client = await ClientService(s).create("Known <client>", telegram_user_id=777)
        projects = [
            await ProjectService(s).create(client.id, f"Проект {i}") for i in range(project_count)
        ]
        archived = await ProjectService(s).create(client.id, "Архив")
        await ProjectService(s).set_archived(archived.id, True)
        cid = client.id
    origin = {
        "type": "user",
        "date": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "sender_user": {"id": 777, "is_bot": False, "first_name": "Known"},
    }
    await h.send("Перенести <домен>\nПодробности & доступ", origin=origin)
    if project_count > 1:
        assert "Выберите проект" in h.last.text
        async with app.db.session_factory() as s:
            assert await s.scalar(select(func.count()).select_from(Task)) == 1
        await h.click("Проект 1")
    assert "Перенести &lt;домен&gt;" in h.last.text
    assert h.button("Главное меню") == "main"
    async with app.db.session_factory() as s:
        task = await s.scalar(select(Task))
        tid = task.id
        assert task.client_id == cid
        assert task.project_id == (projects[-1].id if projects else None)
        assert task.status == "NEW" and task.amount == 0
        assert task.title == "Перенести <домен>"
        assert (
            task.description
            == task.source.original_text
            == "Перенести <домен>\nПодробности & доступ"
        )
        assert task.source.forwarded_user_id == 777
    await h.click("Цена")
    await h.send("19500")
    await h.click("Главное меню")
    assert "Активные задачи: 1" in h.last.text and "19 500 ₽" in h.last.text
    await h.send("Перенести <домен>\nПодробности & доступ", origin=origin)
    assert "уже создана задача" in h.last.text
    async with app.db.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Task)) == 1
        assert (await s.get(Task, tid)).amount == Decimal("19500")


async def test_forward_burst_stays_one_task_while_selecting_project(harness):
    from app.models.task import TaskForwardMessage
    from app.services.client_service import ClientService
    from app.services.project_service import ProjectService

    h = harness
    async with app.db.session_factory.begin() as s:
        client = await ClientService(s).create("Client", telegram_user_id=777)
        for number in range(7):
            await ProjectService(s).create(client.id, f"Проект {number}")
    start = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    origin = {
        "type": "user",
        "date": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "sender_user": {"id": 777, "is_bot": False, "first_name": "Client"},
    }
    await h.send("Первая часть", origin=origin, received_at=start)
    await h.send("Вторая часть", origin=origin, received_at=start + timedelta(seconds=4))
    assert "Добавлено к задаче" in h.last.text
    assert "Первая часть" in h.last.text and "Вторая часть" in h.last.text
    await h.click("➡️")
    await h.click("Проект 6")
    assert "Проект: Проект 6" in h.last.text
    await h.send("Третья часть", origin=origin, received_at=start + timedelta(seconds=8))
    assert "Добавлено к задаче" in h.last.text and "Проект: Проект 6" in h.last.text
    await h.send("Другая задача", origin=origin, received_at=start + timedelta(seconds=13))
    assert "Создано" in h.last.text
    await h.click("Без проекта")
    await h.send("Вторая часть", origin=origin, received_at=start + timedelta(seconds=20))
    assert "уже создана задача" in h.last.text
    async with app.db.session_factory() as s:
        tasks = list((await s.scalars(select(Task).order_by(Task.id))).all())
        assert len(tasks) == 2
        assert tasks[0].description == "Первая часть\n\nВторая часть\n\nТретья часть"
        assert tasks[0].project_id is not None and tasks[1].project_id is None
        assert await s.scalar(select(func.count()).select_from(TaskForwardMessage)) == 4


async def test_draft_debt_visible_and_partial_payment_directly_from_draft(harness):
    from app.services.client_service import ClientService

    h = harness
    async with app.db.session_factory.begin() as s:
        cid = (await ClientService(s).create("Client")).id
        billing = BillingService(s)
        invoice = await billing.current_period(cid, "UTC")
        await billing.add_manual_item(invoice.id, "Работа", Decimal("70000"))
        pid = invoice.id
    await h.send("/start")
    assert "Долг: 70 000 ₽" in h.last.text
    await h.send(callback=f"invoice:{pid}")
    await h.click("Принять оплату")
    await h.send("20000")
    assert "Частично оплачен" in h.last.text
    assert "Оплачено: 20 000 ₽" in h.last.text and "Долг: 50 000 ₽" in h.last.text
    await h.send("/start")
    assert "Долг: 50 000 ₽" in h.last.text
    await h.click("Дашборд")
    assert "Долг: 50 000 ₽" in h.last.text


async def test_invoice_position_can_be_corrected_with_history_after_payment(harness):
    from app.services.client_service import ClientService
    from app.services.payment_service import PaymentService

    h = harness
    async with app.db.session_factory.begin() as s:
        cid = (await ClientService(s).create("Client")).id
        billing = BillingService(s)
        invoice = await billing.current_period(cid, "UTC")
        item = await billing.add_manual_item(invoice.id, "70000", Decimal("70000"))
        await PaymentService(s).add_payment(invoice.id, "20000", issue_draft=True)
        pid, iid = invoice.id, item.id
    await h.send(callback=f"invoice:{pid}")
    await h.click("Корректировать счёт")
    await h.click("70000")
    await h.click("Название")
    await h.send("Разработка <сайта>")
    await h.click("Сохранить")
    assert "Разработка &lt;сайта&gt;" in h.last.text and "Долг: 50 000 ₽" in h.last.text
    await h.send(callback=f"invoice_item:{iid}")
    await h.click("Сумма")
    await h.send("65000")
    await h.click("Сохранить")
    assert "Долг: 45 000 ₽" in h.last.text
    await h.send(callback=f"invoice_item:{iid}")
    await h.click("История изменений")
    assert "70 000 ₽" in h.last.text and "65 000 ₽" in h.last.text
    await h.send("/start")
    assert "Долг: 45 000 ₽" in h.last.text


async def test_server_ip_monthly_amount_edit_and_no_double_billing(harness):
    from app.config import get_settings
    from app.models.recurring import RecurringCharge
    from app.services.client_service import ClientService
    from app.services.recurring_service import RecurringService
    from app.utils.time import current_month

    h = harness
    async with app.db.session_factory.begin() as s:
        cid = (await ClientService(s).create("Client")).id
    await h.send("/start")
    await h.click("Серверы")
    await h.click("➕ Сервер")
    await h.click("Client")
    await h.send("not-an-ip")
    assert "IPv4 или IPv6" in h.last.text
    await h.send("192.0.2.10")
    await h.send("3000")
    await h.click("Сервер")
    assert "192.0.2.10" in h.last.text and "3 000 ₽ / месяц" in h.last.text
    async with app.db.session_factory.begin() as s:
        charge = await s.scalar(select(RecurringCharge))
        assert charge.server_ip == "192.0.2.10" and charge.amount == 3000
        billing = BillingService(s)
        month = current_month(get_settings().timezone)
        await billing.generate_month(*month)
        invoice = (await billing.list_for_client(cid))[0]
        assert len(await billing.items(invoice)) == 1
        assert (await billing.totals(invoice)).invoice_total == 3000
        await billing.issue(invoice)
        pid, charge_id = invoice.id, charge.id
    await h.click("IP")
    await h.send("2001:db8::1")
    await h.click("Сервер")
    assert "2001:db8::1" in h.last.text
    await h.click("Изменить цену")
    await h.send("4000")
    async with app.db.session_factory.begin() as s:
        billing = BillingService(s)
        invoice = await billing.get_period(pid)
        assert (await billing.totals(invoice)).invoice_total == 3000
        assert (await billing.items(invoice))[0].description == "Сервер 192.0.2.10"
        assert (await RecurringService(s).get(charge_id)).amount == 4000
        year, month_no = month
        next_year, next_month = (year + 1, 1) if month_no == 12 else (year, month_no + 1)
        await billing.generate_month(next_year, next_month, cid)
        next_invoice = await billing.get_or_create_period(cid, next_year, next_month)
        assert (await billing.totals(next_invoice)).invoice_total == 4000
