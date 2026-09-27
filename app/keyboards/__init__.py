"""Inline keyboard builders."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Callback prefixes
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


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("👥 Клиенты", CLIENTS), _btn("🔎 Поиск", SEARCH)],
            [_btn("📊 Дашборд (текущий месяц)", "dashboard")],
        ]
    )


def back_to_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("🏠 В меню", MAIN)]])


def clients_list(clients, page: int = 0) -> InlineKeyboardMarkup:
    rows = [[_btn(f"{c.display_name}", f"{CLIENT}:{c.id}")] for c in clients]
    rows.append([_btn("➕ Новый клиент", CLIENT_NEW), _btn("🏠 В меню", MAIN)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_card(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📁 Проекты", f"{PROJECTS}:{client_id}")],
            [_btn("✅ Задачи", f"{TASKS}:{client_id}")],
            [_btn("➕ Дозаказ", f"{DOZ_START}:{client_id}")],
            [_btn("🧾 Счёт за месяц", f"{BILLING}:{client_id}")],
            [_btn("🏠 В меню", MAIN)],
        ]
    )


def projects_list(client_id: int, projects) -> InlineKeyboardMarkup:
    rows = [[_btn(p.name, f"{PROJECT}:{p.id}")] for p in projects]
    rows.append(
        [
            _btn("➕ Новый проект", f"{PROJECT_NEW}:{client_id}"),
            _btn("⬅️ Клиент", f"{CLIENT}:{client_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_actions(project_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Задачи проекта", f"{TASKS}:{project_id}:project")],
            [_btn("⬅️ Назад", MAIN)],
        ]
    )


def tasks_list(client_id: int, tasks, context: str = "client") -> InlineKeyboardMarkup:
    rows = []
    for t in tasks:
        mark = {"NEW": "🆕", "IN_PROGRESS": "⏳", "DONE": "✅", "CANCELLED": "❌"}.get(t.status, "")
        rows.append([_btn(f"{mark} {t.title[:40]}", f"{TASK}:{t.id}")])
    back = f"{CLIENT}:{client_id}" if context == "client" else MAIN
    rows.append([_btn("⬅️ Назад", back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_actions(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("⏳ В работе", f"{TASK_PROGRESS}:{task_id}"),
                _btn("✅ Выполнено", f"{TASK_DONE}:{task_id}"),
            ],
            [_btn("❌ Отменить", f"{TASK_CANCEL}:{task_id}")],
            [_btn("⬅️ В меню", MAIN)],
        ]
    )


def doz_project_picker(client_id: int, projects) -> InlineKeyboardMarkup:
    rows = [[_btn(p.name, f"{DOZ_PROJECT}:{client_id}:{p.id}")] for p in projects]
    rows.append([_btn("Без проекта", f"{DOZ_PROJECT}:{client_id}:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def billing_actions(period_id: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "DRAFT":
        rows.append([_btn("🧾 Выставить счёт", f"{BILLING_ISSUE}:{period_id}")])
    if status in ("ISSUED", "PARTIALLY_PAID"):
        rows.append([_btn("💳 Принять оплату", f"{PAY}:{period_id}")])
    rows.append([_btn("⬅️ В меню", MAIN)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def month_nav(year: int, month: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("⬅️", f"{MONTH}:{year}:{month}:-1"),
                _btn(f"{month:02d}.{year}", "noop"),
                _btn("➡️", f"{MONTH}:{year}:{month}:+1"),
            ],
            [_btn("🏠 В меню", MAIN)],
        ]
    )
