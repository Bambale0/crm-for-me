"""Keep forwarded message groups and per-message duplicate protection durable."""

import sqlalchemy as sa

from alembic import op

revision = "0006_forward_messages"
down_revision = "0005_invoice_transfers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_forward_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("dedup_key", sa.String(255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_task_forward_messages_task_id", "task_forward_messages", ["task_id"])
    op.create_index(
        "uq_task_forward_messages_dedup", "task_forward_messages", ["dedup_key"], unique=True
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM task_forward_messages)")):
        raise RuntimeError("Cannot drop forwarded message history; keep the additive schema.")
    op.drop_table("task_forward_messages")
