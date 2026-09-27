"""BillingPeriod, InvoiceItem and Payment models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import BillingStatus, InvoiceItemSource

if TYPE_CHECKING:
    from app.models.client import Client


class BillingPeriod(TimestampMixin, Base):
    __tablename__ = "billing_periods"
    __table_args__ = (
        CheckConstraint("month >= 1 AND month <= 12", name="ck_billing_periods_month"),
        UniqueConstraint("client_id", "year", "month", name="uq_billing_periods_client_ym"),
        Index("ix_billing_periods_client_id", "client_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..12
    status: Mapped[str] = mapped_column(
        String(20), default=BillingStatus.DRAFT.value, nullable=False
    )
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped["Client"] = relationship()
    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="period", cascade="all, delete-orphan", lazy="selectin"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="period", cascade="all, delete-orphan", lazy="selectin"
    )


class InvoiceItem(TimestampMixin, Base):
    __tablename__ = "invoice_items"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_invoice_items_amount"),
        CheckConstraint("quantity > 0", name="ck_invoice_items_quantity"),
        # Enforces one item per (period, source_type, source_id) when source_id is set.
        Index(
            "uq_invoice_items_period_source",
            "billing_period_id",
            "source_type",
            "source_id",
            unique=True,
        ),
        Index("ix_invoice_items_billing_period_id", "billing_period_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_period_id: Mapped[int] = mapped_column(
        ForeignKey("billing_periods.id", ondelete="RESTRICT"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int | None] = mapped_column(nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("1"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    period: Mapped["BillingPeriod"] = relationship(back_populates="items")

    @classmethod
    def is_source_type(cls, value: str) -> bool:
        return value in {e.value for e in InvoiceItemSource}


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount"),
        CheckConstraint("currency = 'RUB'", name="ck_payments_currency"),
        Index("ix_payments_billing_period_id", "billing_period_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_period_id: Mapped[int] = mapped_column(
        ForeignKey("billing_periods.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    period: Mapped["BillingPeriod"] = relationship(back_populates="payments")
