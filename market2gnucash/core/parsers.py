from __future__ import annotations

import csv
import hashlib
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from market2gnucash.core.date_utils import parse_date
from market2gnucash.core.decimal_utils import ZERO, parse_money, parse_money_required
from market2gnucash.core.models import (
    EbayInputData,
    EbayReportRow,
    EtsyInputData,
    EtsySoldOrderRow,
    EtsyStatementRow,
)

_ORDER_RE = re.compile(r"Order\s*#(\d+)")
_LISTING_RE = re.compile(r"Listing\s*#(\d+)")

_EBAY_HEADER_PREFIX = "Transaction creation date,"
_EBAY_NON_FEE_COLUMNS = {
    "Transaction creation date",
    "Type",
    "Order number",
    "Legacy order ID",
    "Buyer username",
    "Buyer name",
    "Ship to city",
    "Ship to province/region/state",
    "Ship to zip",
    "Ship to country",
    "Net amount",
    "Payout currency",
    "Payout date",
    "Payout ID",
    "Payout method",
    "Payout status",
    "Reason for hold",
    "Item ID",
    "Transaction ID",
    "Item title",
    "Custom label",
    "Quantity",
    "Item subtotal",
    "Shipping and handling",
    "Seller collected tax",
    "eBay collected tax",
    "Gross transaction amount",
    "Transaction currency",
    "Exchange rate",
    "Reference ID",
    "Description",
}


def _hash_row(parts: list[str]) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _within_date_range(row_date: date, start_date: date | None, end_date: date | None) -> bool:
    if start_date and row_date < start_date:
        return False
    if end_date and row_date > end_date:
        return False
    return True


def _extract_order_id(title: str, info: str) -> str | None:
    combined = f"{title} {info}"
    match = _ORDER_RE.search(combined)
    return match.group(1) if match else None


def _extract_listing_id(title: str, info: str) -> str | None:
    combined = f"{title} {info}"
    match = _LISTING_RE.search(combined)
    return match.group(1) if match else None


def parse_etsy_statement(
    path: str | Path,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[EtsyStatementRow, ...]:
    rows: list[EtsyStatementRow] = []

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, raw_row in enumerate(reader, start=1):
            raw = {k.strip(): (v or "").strip() for k, v in raw_row.items() if k is not None}
            row_date = parse_date(raw["Date"])
            if not _within_date_range(row_date, start_date, end_date):
                continue

            row_type = raw["Type"]
            title = raw["Title"]
            info = raw.get("Info", "")
            row = EtsyStatementRow(
                row_id=_hash_row(["etsy_statement", str(index), raw.get("Date", ""), row_type, title, info]),
                row_number=index,
                date=row_date,
                row_type=row_type,
                title=title,
                info=info,
                currency=raw.get("Currency", "USD") or "USD",
                amount=parse_money(raw.get("Amount")),
                fees_taxes=parse_money(raw.get("Fees & Taxes")),
                net=parse_money(raw.get("Net")),
                tax_details=raw.get("Tax Details", ""),
                order_id=_extract_order_id(title, info),
                listing_id=_extract_listing_id(title, info),
                raw=raw,
            )
            rows.append(row)

    return tuple(rows)


def parse_etsy_sold_orders(
    path: str | Path,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[EtsySoldOrderRow, ...]:
    rows: list[EtsySoldOrderRow] = []

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, raw_row in enumerate(reader, start=1):
            raw = {k.strip(): (v or "").strip() for k, v in raw_row.items() if k is not None}
            sale_date = parse_date(raw["Sale Date"])
            if not _within_date_range(sale_date, start_date, end_date):
                continue

            order_id = raw["Order ID"].strip()
            row = EtsySoldOrderRow(
                row_id=_hash_row(["etsy_sold", str(index), order_id, raw.get("Sale Date", "")]),
                row_number=index,
                sale_date=sale_date,
                order_id=order_id,
                currency=raw.get("Currency", "USD") or "USD",
                order_value=parse_money_required(raw.get("Order Value")),
                shipping=parse_money_required(raw.get("Shipping")),
                sales_tax=parse_money_required(raw.get("Sales Tax")),
                order_total=parse_money_required(raw.get("Order Total")),
                raw=raw,
            )
            rows.append(row)

    return tuple(rows)


def parse_etsy_inputs(
    statement_path: str | Path,
    sold_orders_path: str | Path,
    start_date: date | None = None,
    end_date: date | None = None,
) -> EtsyInputData:
    return EtsyInputData(
        statement_rows=parse_etsy_statement(statement_path, start_date, end_date),
        sold_orders=parse_etsy_sold_orders(sold_orders_path, start_date, end_date),
    )


def _find_ebay_header_line(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, line in enumerate(handle):
            if line.startswith(_EBAY_HEADER_PREFIX):
                return index
    raise ValueError(f"Could not find eBay report header line in {path}")


def _is_ebay_fee_column(column_name: str) -> bool:
    if column_name in _EBAY_NON_FEE_COLUMNS:
        return False
    lower = column_name.lower()
    if "collected tax" in lower:
        return False
    return "fee" in lower or "donation" in lower


def parse_ebay_report(
    path: str | Path,
    start_date: date | None = None,
    end_date: date | None = None,
) -> EbayInputData:
    report_path = Path(path)
    header_line_index = _find_ebay_header_line(report_path)

    lines = report_path.read_text(encoding="utf-8-sig").splitlines()
    reader = csv.DictReader(lines[header_line_index:])

    fee_columns = tuple(column for column in reader.fieldnames or [] if _is_ebay_fee_column(column))
    rows: list[EbayReportRow] = []

    for index, raw_row in enumerate(reader, start=1):
        raw = {k.strip(): (v or "").strip() for k, v in raw_row.items() if k is not None}
        row_date = parse_date(raw["Transaction creation date"])
        if not _within_date_range(row_date, start_date, end_date):
            continue

        fee_values: dict[str, Decimal] = {}
        for column_name in fee_columns:
            amount = parse_money(raw.get(column_name))
            if amount is None or amount == ZERO:
                continue
            fee_values[column_name] = amount

        order_number = raw.get("Order number") or None
        if order_number == "--":
            order_number = None

        row = EbayReportRow(
            row_id=_hash_row(
                [
                    "ebay_report",
                    str(index),
                    raw.get("Transaction creation date", ""),
                    raw.get("Type", ""),
                    raw.get("Order number", ""),
                    raw.get("Reference ID", ""),
                    raw.get("Net amount", ""),
                ]
            ),
            row_number=index,
            date=row_date,
            row_type=raw.get("Type", ""),
            order_number=order_number,
            currency=raw.get("Payout currency", "USD") or "USD",
            net_amount=parse_money_required(raw.get("Net amount")),
            item_subtotal=parse_money_required(raw.get("Item subtotal")),
            shipping_and_handling=parse_money_required(raw.get("Shipping and handling")),
            seller_collected_tax=parse_money_required(raw.get("Seller collected tax")),
            ebay_collected_tax=parse_money_required(raw.get("eBay collected tax")),
            fee_columns=fee_values,
            description=raw.get("Description", ""),
            raw=raw,
        )
        rows.append(row)

    return EbayInputData(report_rows=tuple(rows), fee_columns=fee_columns)
