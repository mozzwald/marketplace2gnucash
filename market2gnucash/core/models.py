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
class BankStatementRow:
    row_id: str
    row_number: int
    date: date
    amount: Decimal
    currency: str | None
    description: str
    memo: str
    fitid: str | None
    check_number: str | None
    transaction_type: str | None
    account_id: str | None
    account_name: str | None
    source_path: str
    source_format: str
    raw: Mapping[str, str]


@dataclass(frozen=True)
class BankStatementData:
    source_path: str
    source_format: str
    account_id: str | None
    account_name: str | None
    currency: str | None
    rows: tuple[BankStatementRow, ...]


@dataclass(frozen=True)
class BankCsvProfile:
    has_header: bool = True
    date_column: str | None = None
    amount_column: str | None = None
    debit_column: str | None = None
    credit_column: str | None = None
    description_column: str | None = None
    memo_column: str | None = None
    id_column: str | None = None
    check_number_column: str | None = None
    currency_column: str | None = None
    account_id_column: str | None = None
    account_name_column: str | None = None


@dataclass(frozen=True)
class BankImportSpec:
    account_guid: str | None
    statement_paths: tuple[str, ...]
    csv_profiles: Mapping[str, BankCsvProfile] = field(default_factory=dict)


@dataclass(frozen=True)
class CsvPreviewData:
    path: str
    delimiter: str
    has_header: bool
    columns: tuple[str, ...]
    sample_rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class MappingConfig:
    etsy_clearing_guid: str | None = None
    etsy_income_guid: str | None = None
    etsy_refunds_guid: str | None = None
    ebay_clearing_guid: str | None = None
    ebay_income_guid: str | None = None
    ebay_refunds_guid: str | None = None
    bank_suspense_guid: str | None = None
    etsy_fee_accounts: Mapping[str, str] = field(default_factory=dict)
    ebay_fee_accounts: Mapping[str, str] = field(default_factory=dict)
    bank_match_overrides: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    bank_merchant_accounts: Mapping[str, str] = field(default_factory=dict)
    bank_txn_account_overrides: Mapping[str, str] = field(default_factory=dict)


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
class BankMatchTarget:
    account_guid: str
    amount: Decimal
    memo: str


@dataclass(frozen=True)
class BankMatchResult:
    bank_dedupe_key: str
    bank_txn_id: str
    bank_description: str
    bank_date: date
    bank_amount: Decimal
    status: str
    match_source: str
    matched_transaction_ids: tuple[str, ...]
    targets: tuple[BankMatchTarget, ...]


@dataclass(frozen=True)
class BankCategoryResult:
    bank_dedupe_key: str
    bank_txn_id: str
    merchant_key: str
    description: str
    txn_date: date
    amount: Decimal
    mapped_account_guid: str | None
    mapping_source: str


@dataclass(frozen=True)
class PlanResult:
    transactions: tuple[PlannedTransactionStatus, ...]
    warnings: tuple[str, ...]
    etsy_mapping_keys: tuple[str, ...]
    ebay_fee_columns: tuple[str, ...]
    bank_match_results: tuple[BankMatchResult, ...]
    bank_category_results: tuple[BankCategoryResult, ...]
