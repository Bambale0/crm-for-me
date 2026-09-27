"""Preserve invoice snapshots and lineage when transferring selected positions."""

import sqlalchemy as sa

from alembic import op

revision = "0005_invoice_transfers"
down_revision = "0004_on_demand_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("billing_periods", sa.Column("replaced_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_billing_periods_replacement",
        "billing_periods",
        "billing_periods",
        ["replaced_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("billing_periods", sa.Column("transfer_key", sa.String(64), nullable=True))
    op.add_column("billing_periods", sa.Column("transfer_request", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_billing_periods_transfer_key", "billing_periods", ["transfer_key"]
    )
    op.add_column("invoice_items", sa.Column("origin_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_invoice_items_origin",
        "invoice_items",
        "invoice_items",
        ["origin_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_invoice_items_origin", "invoice_items", ["origin_item_id"])


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM billing_periods WHERE status = 'SUPERSEDED' OR transfer_key IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade after invoice transfers: preserve the invoice lineage."
        )
    op.drop_constraint("uq_invoice_items_origin", "invoice_items", type_="unique")
    op.drop_constraint("fk_invoice_items_origin", "invoice_items", type_="foreignkey")
    op.drop_column("invoice_items", "origin_item_id")
    op.drop_constraint("uq_billing_periods_transfer_key", "billing_periods", type_="unique")
    op.drop_column("billing_periods", "transfer_request")
    op.drop_column("billing_periods", "transfer_key")
    op.drop_constraint("fk_billing_periods_replacement", "billing_periods", type_="foreignkey")
    op.drop_column("billing_periods", "replaced_by_id")
