"""Money formatting helpers. All financial math uses Decimal."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

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


def to_decimal(value) -> Decimal:
    """Coerce user input to Decimal, raising ValueError on bad input."""
    if isinstance(value, Decimal):
        return quantize(value)
    text = str(value).strip().replace(",", ".").replace(" ", "")
    if text == "":
        raise ValueError("empty amount")
    return quantize(Decimal(text))
