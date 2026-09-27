"""FSM states for short dialogs."""

from aiogram.fsm.state import State, StatesGroup


class ClientCreation(StatesGroup):
    waiting_name = State()
    waiting_telegram_id = State()
    waiting_comment = State()


class ProjectCreation(StatesGroup):
    waiting_name = State()


class TaskCreation(StatesGroup):
    waiting_project = State()
    waiting_amount = State()
    waiting_title = State()


class SearchFlow(StatesGroup):
    waiting_query = State()


class PaymentFlow(StatesGroup):
    waiting_amount = State()
