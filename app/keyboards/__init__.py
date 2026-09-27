"""Compact inline navigation with pagination for every entity list."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

MAIN = "main"
CLIENTS = "clients"
CLIENT = "client"
CLIENT_NEW = "client_new"
PROJECTS = "projects"
PROJECT = "project"
PROJECT_NEW = "project_new"
TASKS = "tasks"
TASK = "task"
TASK_DONE = "task_done"
TASK_PROGRESS = "task_progress"
TASK_CANCEL = "task_cancel"
DOZ_START = "doz_start"
DOZ_PROJECT = "doz_project"
BILLING = "billing"
BILLING_ISSUE = "billing_issue"
PAY = "pay"
SEARCH = "search"
MONTH = "month"
PAGE_SIZE = 5


def buttons(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        for label, data in row:
            builder.button(text=label[:64], callback_data=data)
    builder.adjust(*(len(row) for row in rows))
    return builder.as_markup()


def paged(
    entries: list[tuple[str, str]], prefix: str, page: int = 0, extra=None
) -> InlineKeyboardMarkup:
    page = max(0, min(page, max(0, (len(entries) - 1) // PAGE_SIZE)))
    rows = [[entry] for entry in entries[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]]
    nav = []
    if page:
        nav.append(("⬅️", f"{prefix}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < len(entries):
        nav.append(("➡️", f"{prefix}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.extend(extra or [[("🏠 В меню", MAIN)]])
    return buttons(rows)


def main_menu() -> InlineKeyboardMarkup:
    return buttons(
        [
            [("👥 Клиенты", CLIENTS), ("🔎 Поиск", SEARCH)],
            [("📊 Дашборд", "dashboard"), ("⏰ Напоминания", "reminders:0")],
        ]
    )


def back_to_main() -> InlineKeyboardMarkup:
    return buttons([[("🏠 В меню", MAIN)]])


def clients_list(clients, page: int = 0) -> InlineKeyboardMarkup:
    return paged(
        [(c.display_name, f"client:{c.id}") for c in clients],
        "clients",
        page,
        [[("➕ Новый клиент", CLIENT_NEW), ("🏠 В меню", MAIN)]],
    )


def client_card(client_id: int, forward_token: str | None = None) -> InlineKeyboardMarkup:
    start = f"doz_start:{client_id}" + (f":{forward_token}" if forward_token else "")
    return buttons(
        [
            [("➕ Дозаказ", start)],
            [("📁 Проекты", f"projects:{client_id}"), ("✅ Задачи", f"tasks:{client_id}")],
            [("🧾 Счета", f"billing:{client_id}"), ("🔁 Услуги", f"recurring:{client_id}:0")],
            [("📝 Данные и заметки", f"details:client:{client_id}"), ("🏠 В меню", MAIN)],
        ]
    )


def projects_list(client_id: int, projects, page: int = 0) -> InlineKeyboardMarkup:
    return paged(
        [(p.name, f"project:{p.id}") for p in projects],
        f"projects:{client_id}",
        page,
        [[("➕ Новый проект", f"project_new:{client_id}"), ("⬅️ Клиент", f"client:{client_id}")]],
    )


def project_actions(
    project_id: int, client_id: int = 0, status: str = "ACTIVE"
) -> InlineKeyboardMarkup:
    rows = [
        [("✅ Задачи", f"tasks:{project_id}:project")],
        [("📝 Данные и заметки", f"details:project:{project_id}")],
    ]
    if status != "ARCHIVED":
        rows.append([("📦 В архив", f"archive_ask:project:{project_id}")])
    rows.append([("⬅️ Клиент", f"client:{client_id}")])
    return buttons(rows)


def tasks_list(
    client_id: int, tasks, context: str = "client", page: int = 0
) -> InlineKeyboardMarkup:
    marks = {"NEW": "🆕", "IN_PROGRESS": "⏳", "DONE": "✅", "CANCELLED": "❌"}
    return paged(
        [(f"{marks[t.status]} {t.title[:40]}", f"task:{t.id}") for t in tasks],
        f"tasks:{client_id}:{context}",
        page,
        [[("⬅️ Назад", f"{context}:{client_id}")]],
    )


def task_actions(task_id: int, status: str = "NEW", client_id: int = 0) -> InlineKeyboardMarkup:
    rows = []
    if status == "NEW":
        rows.append([("⏳ В работе", f"task_progress:{task_id}")])
    if status in ("NEW", "IN_PROGRESS"):
        rows.extend(
            [
                [("✅ Выполнено", f"task_done:{task_id}")],
                [("❌ Отменить", f"task_cancel_ask:{task_id}")],
            ]
        )
    rows.append(
        [("✏️ Название", f"task_edit:{task_id}:title"), ("💰 Цена", f"task_edit:{task_id}:amount")]
    )
    rows.append([("📝 Описание", f"task_edit:{task_id}:description")])
    rows.append([("⬅️ Задачи", f"tasks:{client_id}")])
    return buttons(rows)


def doz_project_picker(client_id: int, projects, page: int = 0) -> InlineKeyboardMarkup:
    return paged(
        [(p.name, f"doz_project:{client_id}:{p.id}") for p in projects],
        f"doz_page:{client_id}",
        page,
        [
            [("Без проекта", f"doz_project:{client_id}:0")],
            [("➕ Новый проект", f"project_new:{client_id}:doz"), ("🏠 В меню", MAIN)],
        ],
    )


def billing_actions(
    period_id: int, status: str, client_id: int, *, invoice_total, debt
) -> InlineKeyboardMarkup:
    rows = []
    if status == "DRAFT" and invoice_total > 0:
        rows.append([("🧾 Выставить счёт", f"billing_issue_ask:{period_id}")])
    if status in ("ISSUED", "PARTIALLY_PAID") and debt > 0:
        rows.append([("💳 Принять оплату", f"pay:{period_id}")])
    rows.append(
        [("📋 Позиции", f"invoice_items:{period_id}:0"), ("💳 Оплаты", f"payments:{period_id}:0")]
    )
    rows.append([("🧾 История счетов", f"invoice_history:{client_id}:0")])
    if status != "DRAFT":
        rows.append([("➕ Новый счёт", f"billing:{client_id}")])
    rows.append([("⬅️ Клиент", f"client:{client_id}")])
    return buttons(rows)


def month_nav(year: int, month: int) -> InlineKeyboardMarkup:
    return buttons(
        [
            [
                ("⬅️", f"month:{year}:{month}:-1"),
                (f"{month:02d}.{year}", "noop"),
                ("➡️", f"month:{year}:{month}:+1"),
            ],
            [("👥 По клиентам", f"month_clients:{year}:{month}:0"), ("🏠 В меню", MAIN)],
        ]
    )
