from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from market2gnucash.core.date_utils import parse_date
from market2gnucash.core.decimal_utils import ZERO, parse_money, parse_money_required
from market2gnucash.core.models import (
    BankCsvProfile,
    BankStatementData,
    BankStatementRow,
    CsvPreviewData,
    EbayInputData,
    EbayReportRow,
    EtsyInputData,
    EtsySoldOrderRow,
    EtsyStatementRow,
)

_ORDER_RE = re.compile(r"Order\s*#(\d+)")
_LISTING_RE = re.compile(r"Listing\s*#(\d+)")
_OFX_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})")

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

_CSV_DATE_COLUMNS = (
    "date",
    "posting date",
    "posted date",
    "post date",
    "transaction date",
    "trans date",
    "activity date",
)
_CSV_AMOUNT_COLUMNS = ("amount", "transaction amount", "amt", "value")
_CSV_DEBIT_COLUMNS = ("debit", "withdrawal", "charge", "debits")
_CSV_CREDIT_COLUMNS = ("credit", "deposit", "payment", "credits")
_CSV_DESCRIPTION_COLUMNS = (
    "description",
    "transaction",
    "details",
    "payee",
    "merchant",
    "name",
)
_CSV_MEMO_COLUMNS = ("memo", "notes", "note", "category", "details")
_CSV_ID_COLUMNS = ("fitid", "transaction id", "reference", "ref", "id")
_CSV_CHECK_NUMBER_COLUMNS = ("check number", "checknum", "check #", "check")
_CSV_CURRENCY_COLUMNS = ("currency", "curr")
_CSV_ACCOUNT_ID_COLUMNS = ("account id", "account number", "acctid", "account #", "card number")
_CSV_ACCOUNT_NAME_COLUMNS = ("account", "account name", "card name")
_KNOWN_CSV_COLUMNS = (
    _CSV_DATE_COLUMNS
    + _CSV_AMOUNT_COLUMNS
    + _CSV_DEBIT_COLUMNS
    + _CSV_CREDIT_COLUMNS
    + _CSV_DESCRIPTION_COLUMNS
    + _CSV_MEMO_COLUMNS
    + _CSV_ID_COLUMNS
    + _CSV_CHECK_NUMBER_COLUMNS
    + _CSV_CURRENCY_COLUMNS
    + _CSV_ACCOUNT_ID_COLUMNS
    + _CSV_ACCOUNT_NAME_COLUMNS
)


def _hash_row(parts: list[str]) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def _stable_row_id(prefix: str, signature_parts: list[str], occurrence: int) -> str:
    return _hash_row([prefix, *signature_parts, str(occurrence)])


def _assign_occurrence_row_ids(
    rows: list,
    *,
    prefix: str,
    signature_parts,
) -> tuple:
    occurrence_by_signature: dict[tuple[str, ...], int] = {}
    updated_rows = []
    for row in rows:
        signature = tuple(signature_parts(row))
        occurrence = occurrence_by_signature.get(signature, 0) + 1
        occurrence_by_signature[signature] = occurrence
        updated_rows.append(replace(row, row_id=_stable_row_id(prefix, list(signature), occurrence)))
    return tuple(updated_rows)


def _etsy_statement_signature(row: EtsyStatementRow) -> list[str]:
    return [
        row.date.isoformat(),
        row.row_type,
        row.title,
        row.info,
        row.currency,
        _decimal_text(row.amount),
        _decimal_text(row.fees_taxes),
        _decimal_text(row.net),
        row.tax_details,
        row.order_id or "",
        row.listing_id or "",
    ]


def _etsy_sold_order_signature(row: EtsySoldOrderRow) -> list[str]:
    return [
        row.sale_date.isoformat(),
        row.order_id,
        row.currency,
        _decimal_text(row.order_value),
        _decimal_text(row.shipping),
        _decimal_text(row.sales_tax),
        _decimal_text(row.order_total),
    ]


def _ebay_fee_columns_signature(row: EbayReportRow) -> list[str]:
    parts: list[str] = []
    for key, value in sorted(row.fee_columns.items()):
        parts.extend([key, _decimal_text(value)])
    return parts


def _ebay_report_signature(row: EbayReportRow) -> list[str]:
    return [
        row.date.isoformat(),
        row.row_type,
        row.order_number or "",
        row.currency,
        _decimal_text(row.net_amount),
        _decimal_text(row.item_subtotal),
        _decimal_text(row.shipping_and_handling),
        _decimal_text(row.seller_collected_tax),
        _decimal_text(row.ebay_collected_tax),
        row.description,
        row.raw.get("Reference ID", ""),
        row.raw.get("Payout ID", ""),
        row.raw.get("Transaction ID", ""),
        row.raw.get("Legacy order ID", ""),
        *_ebay_fee_columns_signature(row),
    ]


def _bank_statement_signature(row: BankStatementRow) -> list[str]:
    return [
        row.date.isoformat(),
        _decimal_text(row.amount),
        row.currency or "",
        row.description,
        row.memo,
        row.fitid or "",
        row.check_number or "",
        row.transaction_type or "",
        row.account_id or "",
    ]


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


def _read_text_with_fallback(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode statement file {path}")


def _parse_ofx_date(value: str) -> date:
    text = value.strip()
    match = _OFX_DATE_RE.match(text)
    if not match:
        raise ValueError(f"Unsupported OFX date format: {value!r}")
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def _find_first_present(raw: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in raw and raw[candidate]:
            return raw[candidate]
    return None


def _normalize_csv_row(raw_row: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in raw_row.items():
        if key is None:
            continue
        normalized[key.strip().lower()] = (value or "").strip()
    return normalized


def _csv_column_token(index: int) -> str:
    return f"__col_{index}__"


def _normalize_csv_header(value: str) -> str:
    return value.strip().lower()


def _decode_csv_column_token(token: str | None) -> int | None:
    if not token or not token.startswith("__col_") or not token.endswith("__"):
        return None
    middle = token[len("__col_") : -len("__")]
    try:
        return int(middle)
    except ValueError:
        return None


def _csv_profile_from_dict(raw_profile: object) -> BankCsvProfile | None:
    if not isinstance(raw_profile, dict):
        return None
    return BankCsvProfile(
        has_header=bool(raw_profile.get("has_header", True)),
        date_column=raw_profile.get("date_column") if isinstance(raw_profile.get("date_column"), str) else None,
        amount_column=raw_profile.get("amount_column") if isinstance(raw_profile.get("amount_column"), str) else None,
        debit_column=raw_profile.get("debit_column") if isinstance(raw_profile.get("debit_column"), str) else None,
        credit_column=raw_profile.get("credit_column") if isinstance(raw_profile.get("credit_column"), str) else None,
        description_column=raw_profile.get("description_column") if isinstance(raw_profile.get("description_column"), str) else None,
        memo_column=raw_profile.get("memo_column") if isinstance(raw_profile.get("memo_column"), str) else None,
        id_column=raw_profile.get("id_column") if isinstance(raw_profile.get("id_column"), str) else None,
        check_number_column=raw_profile.get("check_number_column") if isinstance(raw_profile.get("check_number_column"), str) else None,
        currency_column=raw_profile.get("currency_column") if isinstance(raw_profile.get("currency_column"), str) else None,
        account_id_column=raw_profile.get("account_id_column") if isinstance(raw_profile.get("account_id_column"), str) else None,
        account_name_column=raw_profile.get("account_name_column") if isinstance(raw_profile.get("account_name_column"), str) else None,
    )


def bank_csv_profile_to_dict(profile: BankCsvProfile) -> dict[str, object]:
    return {
        "has_header": profile.has_header,
        "date_column": profile.date_column,
        "amount_column": profile.amount_column,
        "debit_column": profile.debit_column,
        "credit_column": profile.credit_column,
        "description_column": profile.description_column,
        "memo_column": profile.memo_column,
        "id_column": profile.id_column,
        "check_number_column": profile.check_number_column,
        "currency_column": profile.currency_column,
        "account_id_column": profile.account_id_column,
        "account_name_column": profile.account_name_column,
    }


def _looks_like_headerless_card_csv(rows: list[list[str]]) -> bool:
    if not rows:
        return False
    first = rows[0]
    if len(first) < 3:
        return False
    try:
        parse_date(first[0].strip().strip('"'))
        parse_money_required(first[1].strip().strip('"'))
    except Exception:
        return False
    return True


def inspect_bank_csv_file(path: str | Path) -> CsvPreviewData:
    csv_path = Path(path)
    sample = _read_text_with_fallback(csv_path)
    try:
        dialect = csv.Sniffer().sniff(sample[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows_data = list(csv.reader(io.StringIO(sample), dialect=dialect))
    if not rows_data:
        return CsvPreviewData(
            path=str(csv_path),
            delimiter=getattr(dialect, "delimiter", ","),
            has_header=False,
            columns=(),
            sample_rows=(),
        )

    header_names = tuple(_normalize_csv_header(cell) for cell in rows_data[0])
    has_known_header = any(name in _KNOWN_CSV_COLUMNS for name in header_names)
    has_header = has_known_header and not _looks_like_headerless_card_csv(rows_data)
    if has_header:
        columns = header_names
        sample_rows = tuple(tuple(cell.strip() for cell in row) for row in rows_data[1:6])
    else:
        columns = tuple(_csv_column_token(index) for index in range(len(rows_data[0])))
        sample_rows = tuple(tuple(cell.strip() for cell in row) for row in rows_data[:5])

    return CsvPreviewData(
        path=str(csv_path),
        delimiter=getattr(dialect, "delimiter", ","),
        has_header=has_header,
        columns=columns,
        sample_rows=sample_rows,
    )


def suggest_bank_csv_profile(path: str | Path) -> BankCsvProfile:
    preview = inspect_bank_csv_file(path)
    columns = set(preview.columns)
    if not preview.has_header:
        return BankCsvProfile(
            has_header=False,
            date_column=_csv_column_token(0) if len(preview.columns) >= 1 else None,
            amount_column=_csv_column_token(1) if len(preview.columns) >= 2 else None,
            memo_column=_csv_column_token(3) if len(preview.columns) >= 4 else None,
            description_column=_csv_column_token(4) if len(preview.columns) >= 5 else None,
        )

    def _pick(candidates: tuple[str, ...]) -> str | None:
        for candidate in candidates:
            if candidate in columns:
                return candidate
        return None

    return BankCsvProfile(
        has_header=True,
        date_column=_pick(_CSV_DATE_COLUMNS),
        amount_column=_pick(_CSV_AMOUNT_COLUMNS),
        debit_column=_pick(_CSV_DEBIT_COLUMNS),
        credit_column=_pick(_CSV_CREDIT_COLUMNS),
        description_column=_pick(_CSV_DESCRIPTION_COLUMNS),
        memo_column=_pick(_CSV_MEMO_COLUMNS),
        id_column=_pick(_CSV_ID_COLUMNS),
        check_number_column=_pick(_CSV_CHECK_NUMBER_COLUMNS),
        currency_column=_pick(_CSV_CURRENCY_COLUMNS),
        account_id_column=_pick(_CSV_ACCOUNT_ID_COLUMNS),
        account_name_column=_pick(_CSV_ACCOUNT_NAME_COLUMNS),
    )


def _value_from_profile(raw: dict[str, str], column_name: str | None) -> str | None:
    if not column_name:
        return None
    value = raw.get(column_name)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _build_csv_row_map(
    values: list[str],
    columns: tuple[str, ...],
) -> dict[str, str]:
    raw: dict[str, str] = {}
    for index, column in enumerate(columns):
        raw[column] = values[index].strip() if index < len(values) else ""
    return raw


def _parse_profiled_bank_csv(
    path: Path,
    rows_data: list[list[str]],
    start_date: date | None,
    end_date: date | None,
    profile: BankCsvProfile,
) -> BankStatementData:
    if not rows_data:
        raise ValueError(f"CSV statement file {path} is empty")

    if profile.has_header:
        columns = tuple(_normalize_csv_header(cell) for cell in rows_data[0])
        data_rows = rows_data[1:]
    else:
        columns = tuple(_csv_column_token(index) for index in range(len(rows_data[0])))
        data_rows = rows_data

    rows: list[BankStatementRow] = []
    currency_values: set[str] = set()
    account_id: str | None = None
    account_name: str | None = None

    for index, values in enumerate(data_rows, start=1):
        cols = [(value or "").strip() for value in values]
        if not any(cols):
            continue
        raw = _build_csv_row_map(cols, columns)

        date_text = _value_from_profile(raw, profile.date_column)
        if not date_text:
            raise ValueError(f"CSV statement file {path} is missing a date value for row {index}")

        row_date = parse_date(date_text)
        if not _within_date_range(row_date, start_date, end_date):
            continue

        amount_text = _value_from_profile(raw, profile.amount_column)
        if amount_text:
            amount = parse_money_required(amount_text)
        else:
            debit = parse_money_required(_value_from_profile(raw, profile.debit_column))
            credit = parse_money_required(_value_from_profile(raw, profile.credit_column))
            amount = credit - debit

        currency = _value_from_profile(raw, profile.currency_column)
        if currency:
            currency_values.add(currency)
        if account_id is None:
            account_id = _value_from_profile(raw, profile.account_id_column)
        if account_name is None:
            account_name = _value_from_profile(raw, profile.account_name_column)

        description = _value_from_profile(raw, profile.description_column) or ""
        memo = _value_from_profile(raw, profile.memo_column) or ""
        fitid = _value_from_profile(raw, profile.id_column)
        check_number = _value_from_profile(raw, profile.check_number_column)

        rows.append(
            BankStatementRow(
                row_id="",
                row_number=index,
                date=row_date,
                amount=amount,
                currency=currency,
                description=description,
                memo=memo,
                fitid=fitid,
                check_number=check_number,
                transaction_type=None,
                account_id=account_id,
                account_name=account_name or path.stem,
                source_path=str(path),
                source_format="csv",
                raw=raw,
            )
        )

    return BankStatementData(
        source_path=str(path),
        source_format="csv",
        account_id=account_id,
        account_name=account_name or path.stem,
        currency=next(iter(currency_values)) if len(currency_values) == 1 else None,
        rows=_assign_occurrence_row_ids(
            rows,
            prefix="bank_csv_profile",
            signature_parts=_bank_statement_signature,
        ),
    )


def _parse_bank_csv(
    path: Path,
    start_date: date | None,
    end_date: date | None,
    csv_profile: BankCsvProfile | None = None,
) -> BankStatementData:
    sample = _read_text_with_fallback(path)
    try:
        dialect = csv.Sniffer().sniff(sample[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows_data = list(csv.reader(io.StringIO(sample), dialect=dialect))
    if not rows_data:
        raise ValueError(f"CSV statement file {path} is empty")

    if csv_profile is not None:
        return _parse_profiled_bank_csv(path, rows_data, start_date, end_date, csv_profile)

    header_names = [cell.strip().lower() for cell in rows_data[0]]
    has_known_header = any(name in _KNOWN_CSV_COLUMNS for name in header_names)
    if not has_known_header and _looks_like_headerless_card_csv(rows_data):
        return _parse_profiled_bank_csv(
            path,
            rows_data,
            start_date,
            end_date,
            BankCsvProfile(
                has_header=False,
                date_column=_csv_column_token(0),
                amount_column=_csv_column_token(1),
                memo_column=_csv_column_token(3),
                description_column=_csv_column_token(4),
            ),
        )

    rows: list[BankStatementRow] = []
    currency_values: set[str] = set()
    account_id: str | None = None
    account_name: str | None = None

    reader = csv.DictReader(io.StringIO(sample), dialect=dialect)
    if reader.fieldnames is None:
        raise ValueError(f"CSV statement file {path} has no header row")

    for index, raw_row in enumerate(reader, start=1):
        raw = _normalize_csv_row(raw_row)
        date_text = _find_first_present(raw, _CSV_DATE_COLUMNS)
        if not date_text:
            raise ValueError(f"CSV statement file {path} is missing a recognizable date column")
        row_date = parse_date(date_text)
        if not _within_date_range(row_date, start_date, end_date):
            continue

        amount_text = _find_first_present(raw, _CSV_AMOUNT_COLUMNS)
        if amount_text:
            amount = parse_money_required(amount_text)
        else:
            debit = parse_money_required(_find_first_present(raw, _CSV_DEBIT_COLUMNS))
            credit = parse_money_required(_find_first_present(raw, _CSV_CREDIT_COLUMNS))
            amount = credit - debit

        currency = _find_first_present(raw, _CSV_CURRENCY_COLUMNS)
        if currency:
            currency_values.add(currency)
        if account_id is None:
            account_id = _find_first_present(raw, _CSV_ACCOUNT_ID_COLUMNS)
        if account_name is None:
            account_name = _find_first_present(raw, _CSV_ACCOUNT_NAME_COLUMNS)

        description = _find_first_present(raw, _CSV_DESCRIPTION_COLUMNS) or ""
        memo = _find_first_present(raw, _CSV_MEMO_COLUMNS) or ""
        fitid = _find_first_present(raw, _CSV_ID_COLUMNS)
        check_number = _find_first_present(raw, _CSV_CHECK_NUMBER_COLUMNS)

        rows.append(
            BankStatementRow(
                row_id="",
                row_number=index,
                date=row_date,
                amount=amount,
                currency=currency,
                description=description,
                memo=memo,
                fitid=fitid,
                check_number=check_number,
                transaction_type=None,
                account_id=account_id,
                account_name=account_name or path.stem,
                source_path=str(path),
                source_format="csv",
                raw=raw,
            )
        )

    return BankStatementData(
        source_path=str(path),
        source_format="csv",
        account_id=account_id,
        account_name=account_name or path.stem,
        currency=next(iter(currency_values)) if len(currency_values) == 1 else None,
        rows=_assign_occurrence_row_ids(
            rows,
            prefix="bank_csv",
            signature_parts=_bank_statement_signature,
        ),
    )


def _ofx_text_value(block: str, tag_name: str) -> str | None:
    match = re.search(rf"<{tag_name}>([^<\r\n]+)", block, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _xml_local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1].lower()
    return tag.lower()


def _xml_child_text(element: ET.Element, tag_name: str) -> str | None:
    for child in element.iter():
        if _xml_local_name(child.tag) == tag_name.lower():
            value = (child.text or "").strip()
            if value:
                return value
    return None


def _parse_bank_ofx(
    path: Path,
    start_date: date | None,
    end_date: date | None,
) -> BankStatementData:
    text = _read_text_with_fallback(path)
    ofx_start = text.upper().find("<OFX")
    if ofx_start == -1:
        raise ValueError(f"OFX statement file {path} does not contain an <OFX> payload")
    payload = text[ofx_start:]

    account_id = None
    account_name = path.stem
    currency = None
    rows: list[BankStatementRow] = []

    try:
        xml_root = ET.fromstring(payload)
        currency = _xml_child_text(xml_root, "CURDEF")
        account_id = _xml_child_text(xml_root, "ACCTID")

        transactions = [element for element in xml_root.iter() if _xml_local_name(element.tag) == "stmttrn"]
        for index, transaction in enumerate(transactions, start=1):
            posted = _xml_child_text(transaction, "DTPOSTED")
            amount_text = _xml_child_text(transaction, "TRNAMT")
            if not posted or not amount_text:
                continue

            row_date = _parse_ofx_date(posted)
            if not _within_date_range(row_date, start_date, end_date):
                continue

            fitid = _xml_child_text(transaction, "FITID")
            description = _xml_child_text(transaction, "NAME") or ""
            memo = _xml_child_text(transaction, "MEMO") or ""
            trntype = _xml_child_text(transaction, "TRNTYPE")
            check_number = _xml_child_text(transaction, "CHECKNUM")
            amount = parse_money_required(amount_text)

            rows.append(
                BankStatementRow(
                    row_id="",
                    row_number=index,
                    date=row_date,
                    amount=amount,
                    currency=currency,
                    description=description,
                    memo=memo,
                    fitid=fitid,
                    check_number=check_number,
                    transaction_type=trntype,
                    account_id=account_id,
                    account_name=account_name,
                    source_path=str(path),
                    source_format="ofx",
                    raw={
                        "dtposted": posted,
                        "trnamt": amount_text,
                        "fitid": fitid or "",
                        "name": description,
                        "memo": memo,
                        "trntype": trntype or "",
                        "checknum": check_number or "",
                    },
                )
            )
    except ET.ParseError:
        currency = _ofx_text_value(payload, "CURDEF")
        account_id = _ofx_text_value(payload, "ACCTID")
        blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", payload, flags=re.IGNORECASE | re.DOTALL)
        for index, block in enumerate(blocks, start=1):
            posted = _ofx_text_value(block, "DTPOSTED")
            amount_text = _ofx_text_value(block, "TRNAMT")
            if not posted or not amount_text:
                continue

            row_date = _parse_ofx_date(posted)
            if not _within_date_range(row_date, start_date, end_date):
                continue

            fitid = _ofx_text_value(block, "FITID")
            description = _ofx_text_value(block, "NAME") or ""
            memo = _ofx_text_value(block, "MEMO") or ""
            trntype = _ofx_text_value(block, "TRNTYPE")
            check_number = _ofx_text_value(block, "CHECKNUM")
            amount = parse_money_required(amount_text)

            rows.append(
                BankStatementRow(
                    row_id="",
                    row_number=index,
                    date=row_date,
                    amount=amount,
                    currency=currency,
                    description=description,
                    memo=memo,
                    fitid=fitid,
                    check_number=check_number,
                    transaction_type=trntype,
                    account_id=account_id,
                    account_name=account_name,
                    source_path=str(path),
                    source_format="ofx",
                    raw={
                        "dtposted": posted,
                        "trnamt": amount_text,
                        "fitid": fitid or "",
                        "name": description,
                        "memo": memo,
                        "trntype": trntype or "",
                        "checknum": check_number or "",
                    },
                )
            )

    return BankStatementData(
        source_path=str(path),
        source_format="ofx",
        account_id=account_id,
        account_name=account_name,
        currency=currency,
        rows=_assign_occurrence_row_ids(
            rows,
            prefix="bank_ofx",
            signature_parts=_bank_statement_signature,
        ),
    )


def parse_bank_statement_file(
    path: str | Path,
    start_date: date | None = None,
    end_date: date | None = None,
    csv_profile: BankCsvProfile | dict[str, object] | None = None,
) -> BankStatementData:
    statement_path = Path(path)
    suffix = statement_path.suffix.lower()
    normalized_profile = (
        csv_profile
        if isinstance(csv_profile, BankCsvProfile)
        else _csv_profile_from_dict(csv_profile)
    )
    if suffix in {".ofx", ".qfx"}:
        return _parse_bank_ofx(statement_path, start_date, end_date)
    if suffix == ".csv":
        return _parse_bank_csv(statement_path, start_date, end_date, normalized_profile)
    raise ValueError(f"Unsupported bank/card statement format for {statement_path}")


def parse_bank_statement_files(
    paths: list[str] | tuple[str, ...],
    start_date: date | None = None,
    end_date: date | None = None,
    csv_profiles: dict[str, BankCsvProfile | dict[str, object]] | None = None,
) -> tuple[BankStatementData, ...]:
    parsed_files: list[BankStatementData] = []
    for path in paths:
        parsed_files.append(parse_bank_statement_file(path, start_date, end_date, (csv_profiles or {}).get(path)))
    return tuple(parsed_files)


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
                row_id="",
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

    return _assign_occurrence_row_ids(
        rows,
        prefix="etsy_statement",
        signature_parts=_etsy_statement_signature,
    )


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
                row_id="",
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

    return _assign_occurrence_row_ids(
        rows,
        prefix="etsy_sold",
        signature_parts=_etsy_sold_order_signature,
    )


def _as_path_tuple(paths: str | Path | Sequence[str | Path]) -> tuple[str | Path, ...]:
    if isinstance(paths, (str, Path)):
        return (paths,)
    return tuple(paths)


def parse_etsy_statement_files(
    paths: str | Path | Sequence[str | Path],
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[EtsyStatementRow, ...]:
    rows: list[EtsyStatementRow] = []
    for path in _as_path_tuple(paths):
        rows.extend(parse_etsy_statement(path, start_date, end_date))
    return _assign_occurrence_row_ids(
        rows,
        prefix="etsy_statement",
        signature_parts=_etsy_statement_signature,
    )


def parse_etsy_sold_order_files(
    paths: str | Path | Sequence[str | Path],
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[EtsySoldOrderRow, ...]:
    rows: list[EtsySoldOrderRow] = []
    for path in _as_path_tuple(paths):
        rows.extend(parse_etsy_sold_orders(path, start_date, end_date))
    return _assign_occurrence_row_ids(
        rows,
        prefix="etsy_sold",
        signature_parts=_etsy_sold_order_signature,
    )


def parse_etsy_inputs(
    statement_path: str | Path | Sequence[str | Path],
    sold_orders_path: str | Path | Sequence[str | Path],
    start_date: date | None = None,
    end_date: date | None = None,
) -> EtsyInputData:
    return EtsyInputData(
        statement_rows=parse_etsy_statement_files(statement_path, start_date, end_date),
        sold_orders=parse_etsy_sold_order_files(sold_orders_path, start_date, end_date),
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
            row_id="",
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

    return EbayInputData(
        report_rows=_assign_occurrence_row_ids(
            rows,
            prefix="ebay_report",
            signature_parts=_ebay_report_signature,
        ),
        fee_columns=fee_columns,
    )
