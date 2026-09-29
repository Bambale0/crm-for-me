"""Append-only invoice position corrections preserve every original snapshot."""

import sqlalchemy as sa

from alembic import op

revision = "0008_invoice_corrections"
down_revision = "0007_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_item_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "invoice_item_id",
            sa.Integer(),
            sa.ForeignKey("invoice_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("previous_description", sa.Text(), nullable=False),
        sa.Column("previous_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("operation_key", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount >= 0", name="ck_invoice_correction_amount"),
    )
    op.create_index(
        "ix_invoice_item_corrections_item_id", "invoice_item_corrections", ["invoice_item_id"]
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM invoice_item_corrections)")):
        raise RuntimeError("Cannot drop financial correction history")
    op.drop_table("invoice_item_corrections")
