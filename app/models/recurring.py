"""RecurringCharge model."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import RecurringFrequency

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.project import Project


class RecurringCharge(TimestampMixin, Base):
    __tablename__ = "recurring_charges"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_recurring_amount"),
        CheckConstraint("currency = 'RUB'", name="ck_recurring_currency"),
        CheckConstraint(
            "active_until IS NULL OR active_until >= active_from", name="ck_recurring_dates"
        ),
        Index("ix_recurring_charges_client_id", "client_id"),
        Index("ix_recurring_charges_project_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    server_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    frequency: Mapped[str] = mapped_column(
        String(16), default=RecurringFrequency.MONTHLY.value, nullable=False
    )
    active_from: Mapped[date] = mapped_column(Date, nullable=False)
    active_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    client: Mapped["Client"] = relationship()
    project: Mapped["Project | None"] = relationship()
