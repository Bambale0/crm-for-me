"""Payment service: partial/full payments and status updates."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Payment
from app.models.enums import BillingStatus
from app.repositories.billing import BillingRepository
from app.services.billing_service import BillingService
from app.services.errors import InvalidAmountError, InvalidTransitionError, OverpaymentError
from app.utils.money import to_decimal, validate_currency
from app.utils.time import now_utc


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BillingRepository(session)
        self.billing = BillingService(session)

    async def add_payment(
        self,
        period_id: int,
        amount: Decimal | str,
        *,
        currency: str = "RUB",
        paid_at: datetime | None = None,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> Payment:
        amount = to_decimal(amount)
        currency = validate_currency(currency)
        period = await self.billing.lock_period(period_id)
        if idempotency_key:
            existing = await self.session.scalar(
                select(Payment).where(Payment.idempotency_key == idempotency_key)
            )
            if existing is not None:
                if (
                    existing.billing_period_id != period_id
                    or existing.amount != amount
                    or existing.currency != currency
                ):
                    raise ValueError("Эта операция уже использована для другого платежа")
                return existing
        if period.status not in (
            BillingStatus.ISSUED.value,
            BillingStatus.PARTIALLY_PAID.value,
        ):
            raise InvalidTransitionError(
                f"Cannot pay a period in status {period.status}; issue it first"
            )

        if amount <= 0:
            raise InvalidAmountError("Payment amount must be positive")

        totals = await self.billing.totals(period)
        remaining = totals.invoice_total - totals.paid_total
        if amount > remaining:
            raise OverpaymentError(f"Payment {amount} exceeds remaining debt {remaining}")

        payment = Payment(
            billing_period_id=period_id,
            amount=amount,
            currency=currency,
            paid_at=paid_at or now_utc(),
            comment=(comment or None),
            idempotency_key=idempotency_key,
        )
        await self.repo.add_payment(payment)

        new_paid = totals.paid_total + amount
        if new_paid >= totals.invoice_total:
            period.status = BillingStatus.PAID.value
            period.closed_at = now_utc()
        else:
            period.status = BillingStatus.PARTIALLY_PAID.value

        await self.session.flush()
        return payment
