from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Mapping


@dataclass(frozen=True)
class AccountRecord:
    guid: str
    name: str
    account_type: str
    parent_guid: str | None
    full_name: str


@dataclass(frozen=True)
class BookInfo:
    path: str
    book_id: str
    lock_files: tuple[str, ...]
    accounts: tuple[AccountRecord, ...]


@dataclass(frozen=True)
class EtsyStatementRow:
    row_id: str
    row_number: int
    date: date
    row_type: str
    title: str
    info: str
    currency: str
    amount: Decimal | None
    fees_taxes: Decimal | None
    net: Decimal | None
    tax_details: str
    order_id: str | None
    listing_id: str | None
    raw: Mapping[str, str]


@dataclass(frozen=True)
class EtsySoldOrderRow:
    row_id: str
    row_number: int
    sale_date: date
    order_id: str
    currency: str
    order_value: Decimal
    shipping: Decimal
    sales_tax: Decimal
    order_total: Decimal
    raw: Mapping[str, str]


@dataclass(frozen=True)
class EbayReportRow:
    row_id: str
    row_number: int
    date: date
    row_type: str
    order_number: str | None
    currency: str
    net_amount: Decimal
    item_subtotal: Decimal
    shipping_and_handling: Decimal
    seller_collected_tax: Decimal
    ebay_collected_tax: Decimal
    fee_columns: Mapping[str, Decimal]
    description: str
    raw: Mapping[str, str]


@dataclass(frozen=True)
class EtsyInputData:
    statement_rows: tuple[EtsyStatementRow, ...]
    sold_orders: tuple[EtsySoldOrderRow, ...]


@dataclass(frozen=True)
class EbayInputData:
    report_rows: tuple[EbayReportRow, ...]
    fee_columns: tuple[str, ...]


@dataclass(frozen=True)
class MappingConfig:
    etsy_clearing_guid: str | None = None
    etsy_income_guid: str | None = None
    etsy_refunds_guid: str | None = None
    ebay_clearing_guid: str | None = None
    ebay_income_guid: str | None = None
    ebay_refunds_guid: str | None = None
    etsy_fee_accounts: Mapping[str, str] = field(default_factory=dict)
    ebay_fee_accounts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlannedSplit:
    account_guid: str | None
    amount: Decimal
    memo: str
    mapping_key: str | None = None


@dataclass(frozen=True)
class PlannedTransaction:
    dedupe_key: str
    marketplace: str
    txn_kind: str
    txn_id: str
    date: date
    description: str
    external_ref: str
    clearing_amount: Decimal
    splits: tuple[PlannedSplit, ...]
    source_row_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedTransactionStatus:
    transaction: PlannedTransaction
    status: str
    status_reason: str


@dataclass(frozen=True)
class PlanResult:
    transactions: tuple[PlannedTransactionStatus, ...]
    warnings: tuple[str, ...]
    etsy_mapping_keys: tuple[str, ...]
    ebay_fee_columns: tuple[str, ...]
