"""Complete task billing assignment and durable payment idempotency.

Revision ID: 0002_financial_integrity
Revises: 0001_initial
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_financial_integrity"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("billing_period_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_billing_period",
        "tasks",
        "billing_periods",
        ["billing_period_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("payments", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_payments_idempotency_key", "payments", ["idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_payments_idempotency_key", "payments", type_="unique")
    op.drop_column("payments", "idempotency_key")
    op.drop_constraint("fk_tasks_billing_period", "tasks", type_="foreignkey")
    op.drop_column("tasks", "billing_period_id")
