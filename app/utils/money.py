"""Money formatting helpers. All financial math uses Decimal."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CURRENCY_SYMBOLS = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
}


def quantize(amount: Decimal) -> Decimal:
    """Round to 2 decimal places using banker-neutral HALF_UP."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(amount: Decimal, currency: str = "RUB") -> str:
    """Format a Decimal amount as a human string, e.g. 5000 -> '5 000 ₽'."""
    amount = quantize(amount)
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    if amount == amount.to_integral_value():
        body = f"{amount:,.0f}".replace(",", " ")
    else:
        body = f"{amount:,.2f}".replace(",", " ")
    return f"{body} {symbol}".strip()


def to_decimal(value: Decimal | str | int) -> Decimal:
    """Validate a nonnegative, finite NUMERIC(18, 2) amount, never via float."""
    if isinstance(value, (float, bool)):
        raise ValueError("Введите сумму числом, например 1500,50")
    try:
        amount = Decimal(
            str(value).strip().replace(",", ".").replace(" ", "").replace("\u00a0", "")
        )
        if not amount.is_finite() or amount < 0:
            raise ValueError("Сумма должна быть конечной и неотрицательной")
        amount = quantize(amount)
        if amount >= Decimal("10000000000000000"):
            raise ValueError("Сумма слишком велика")
        return amount
    except InvalidOperation as exc:
        raise ValueError("Введите сумму числом, например 1500,50") from exc


def validate_currency(currency: str) -> str:
    if currency != "RUB":
        raise ValueError("В этой версии доступен учёт только в RUB")
    return currency
