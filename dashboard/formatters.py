from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation


def parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid decimal type")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite decimal value")
        raise ValueError("invalid decimal type")
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("malformed decimal value") from exc
    else:
        raise ValueError("invalid decimal type")
    if not amount.is_finite():
        raise ValueError("nonfinite decimal value")
    return amount


def format_currency(value: object) -> str:
    amount = parse_decimal(value)
    if amount is None:
        return "Unavailable"
    sign = "-" if amount < 0 else ""
    amount = abs(amount).quantize(Decimal("0.01"))
    return f"{sign}${amount:,.2f}"


def format_quantity(value: object) -> str:
    quantity = parse_decimal(value)
    if quantity is None:
        return "Unavailable"
    normalized = quantity.normalize()
    return format(normalized, "f")


def format_percent(value: object) -> str:
    percent = parse_decimal(value)
    if percent is None:
        return "Unavailable"
    rounded = percent.quantize(Decimal("0.01"))
    if rounded == rounded.to_integral_value():
        return f"{rounded.quantize(Decimal('1'))}%"
    return f"{rounded.normalize()}%"


def format_date(value: object) -> str:
    text = str(value or "").strip()
    return text or "Unavailable"


def signed_label(value: object) -> str:
    amount = parse_decimal(value)
    if amount is None:
        return "Unavailable"
    if amount > 0:
        return f"+{format_currency(amount)}"
    if amount < 0:
        return f"-{format_currency(abs(amount))}"
    return "$0.00"
