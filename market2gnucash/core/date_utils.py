from __future__ import annotations

from datetime import date, datetime

_DATE_PATTERNS = (
    "%B %d, %Y",  # Etsy statement
    "%b %d, %Y",  # eBay report
    "%m/%d/%y",   # Etsy sold orders short
    "%m/%d/%Y",   # Etsy sold orders long
)


def parse_date(value: str) -> date:
    text = value.strip()
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value!r}")
