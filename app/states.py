"""Short dialogs; committed business data lives in PostgreSQL."""

from aiogram.fsm.state import State, StatesGroup


class ClientCreation(StatesGroup):
    waiting_name = State()


class ProjectCreation(StatesGroup):
    waiting_name = State()


class TaskCreation(StatesGroup):
    quick_title = State()
    quick_amount = State()
    waiting_project = State()
    waiting_amount = State()
    waiting_title = State()


class SearchFlow(StatesGroup):
    waiting_query = State()


class PaymentFlow(StatesGroup):
    waiting_amount = State()


class EditFlow(StatesGroup):
    value = State()


class FieldFlow(StatesGroup):
    name = State()
    value = State()


class NoteFlow(StatesGroup):
    text = State()


class RecurringFlow(StatesGroup):
    title = State()
    amount = State()
    start = State()


class ReminderFlow(StatesGroup):
    text = State()
    when = State()


class InvoiceTransferFlow(StatesGroup):
    selecting = State()
    confirming = State()


class ServerFlow(StatesGroup):
    ip = State()
    amount = State()


class InvoiceItemEdit(StatesGroup):
    value = State()
    confirm = State()
