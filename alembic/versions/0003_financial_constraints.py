"""Reject invalid financial rows and protect history from cascaded deletes."""

from alembic import op

revision = "0003_financial_constraints"
down_revision = "0002_financial_integrity"
branch_labels = None
depends_on = None

CHECKS = [
    ("billing_periods", "ck_billing_periods_month", "month >= 1 AND month <= 12"),
    ("invoice_items", "ck_invoice_items_amount", "amount >= 0"),
    ("invoice_items", "ck_invoice_items_quantity", "quantity > 0"),
    ("payments", "ck_payments_amount", "amount > 0"),
    ("payments", "ck_payments_currency", "currency = 'RUB'"),
    ("tasks", "ck_tasks_amount", "amount >= 0"),
    ("tasks", "ck_tasks_currency", "currency = 'RUB'"),
    ("recurring_charges", "ck_recurring_amount", "amount >= 0"),
    ("recurring_charges", "ck_recurring_currency", "currency = 'RUB'"),
    (
        "recurring_charges",
        "ck_recurring_dates",
        "active_until IS NULL OR active_until >= active_from",
    ),
]
FOREIGN_KEYS = [
    ("billing_periods", "client_id", "clients"),
    ("invoice_items", "billing_period_id", "billing_periods"),
    ("payments", "billing_period_id", "billing_periods"),
]


def _foreign_keys(ondelete: str) -> None:
    for table, column, target in FOREIGN_KEYS:
        name = f"{table}_{column}_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, target, [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    for table, name, condition in CHECKS:
        op.create_check_constraint(name, table, condition)
    _foreign_keys("RESTRICT")


def downgrade() -> None:
    _foreign_keys("CASCADE")
    for table, name, _ in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
