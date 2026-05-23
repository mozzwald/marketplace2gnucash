from __future__ import annotations

from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")


def parse_money(value: str | None) -> Decimal | None:
    if value is None:
        return None

    text = value.strip()
    if not text or text == "--":
        return None

    normalized = (
        text.replace("$", "")
        .replace(",", "")
        .replace("(", "-")
        .replace(")", "")
        .replace(" ", "")
    )

    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid money value: {value!r}") from exc


def parse_money_required(value: str | None) -> Decimal:
    parsed = parse_money(value)
    if parsed is None:
        return ZERO
    return parsed
