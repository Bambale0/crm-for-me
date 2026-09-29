"""Store server IP beside its existing recurring monthly charge."""

import sqlalchemy as sa

from alembic import op

revision = "0007_servers"
down_revision = "0006_forward_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recurring_charges", sa.Column("server_ip", sa.String(45), nullable=True))


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM recurring_charges WHERE server_ip IS NOT NULL)")
    ):
        raise RuntimeError("Cannot drop saved server addresses")
    op.drop_column("recurring_charges", "server_ip")
