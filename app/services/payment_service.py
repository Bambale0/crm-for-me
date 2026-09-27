"""Payment service: partial/full payments and status updates."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Payment
from app.models.enums import BillingStatus
from app.repositories.billing import BillingRepository
from app.services.billing_service import BillingService
from app.services.errors import InvalidAmountError, InvalidTransitionError, OverpaymentError
from app.utils.money import quantize
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
    ) -> Payment:
        period = await self.billing.get_period(period_id)
        if period.status not in (
            BillingStatus.ISSUED.value,
            BillingStatus.PARTIALLY_PAID.value,
        ):
            raise InvalidTransitionError(
                f"Cannot pay a period in status {period.status}; issue it first"
            )

        amount = quantize(Decimal(str(amount)))
        if amount <= 0:
            raise InvalidAmountError("Payment amount must be positive")

        totals = await self.billing.totals(period)
        remaining = totals.invoice_total - totals.paid_total
        if amount > remaining:
            raise OverpaymentError(
                f"Payment {amount} exceeds remaining debt {remaining}"
            )

        payment = Payment(
            billing_period_id=period_id,
            amount=amount,
            currency=currency,
            paid_at=paid_at or now_utc(),
            comment=(comment or None),
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
