"""Allow multiple invoices per accounting month while keeping one open draft."""

import sqlalchemy as sa

from alembic import op

revision = "0004_on_demand_invoices"
down_revision = "0003_financial_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_billing_periods_client_ym", "billing_periods", type_="unique")
    op.create_index(
        "uq_billing_periods_draft_client_ym",
        "billing_periods",
        ["client_id", "year", "month"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )


def downgrade() -> None:
    # Downgrading after invoices were created must fail without deleting history.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM billing_periods GROUP BY client_id, year, month HAVING count(*) > 1)"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade: multiple invoices exist for a month. Preserve data and restore a reviewed backup instead."
        )
    op.drop_index("uq_billing_periods_draft_client_ym", table_name="billing_periods")
    op.create_unique_constraint(
        "uq_billing_periods_client_ym", "billing_periods", ["client_id", "year", "month"]
    )
