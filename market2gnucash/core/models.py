from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


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
    legacy_row_ids: tuple[str, ...] = ()


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
    statement_directory: str | None = None
    statement_paths: tuple[str, ...] = ()
    csv_profile: BankCsvProfile | None = None
    csv_profiles: Mapping[str, BankCsvProfile] = field(default_factory=dict)


@dataclass(frozen=True)
class EtsyMonthlyExport:
    statement_path: str | None = None
    sold_orders_path: str | None = None


@dataclass(frozen=True)
class MarketplaceImportSpec:
    import_id: str
    marketplace: str
    account_key: str
    account_label: str
    etsy_statement_path: str | None = None
    etsy_sold_orders_path: str | None = None
    etsy_monthly_exports: tuple[EtsyMonthlyExport, ...] = ()
    ebay_report_path: str | None = None
    ebay_report_directory: str | None = None


@dataclass(frozen=True)
class CsvPreviewData:
    path: str
    delimiter: str
    has_header: bool
    columns: tuple[str, ...]
    sample_rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class MappingConfig:
    marketplace_accounts: Mapping[str, MarketplaceAccountMapping] = field(default_factory=dict)
    bank_match_overrides: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    bank_transfer_overrides: Mapping[str, str] = field(default_factory=dict)
    bank_merchant_accounts: Mapping[str, str] = field(default_factory=dict)
    bank_txn_account_overrides: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketplaceAccountMapping:
    marketplace: str
    account_label: str
    clearing_guid: str | None = None
    income_guid: str | None = None
    refunds_guid: str | None = None
    fee_accounts: Mapping[str, str] = field(default_factory=dict)


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
    marketplace_account_key: str | None
    marketplace_account_label: str | None
    txn_kind: str
    txn_id: str
    date: date
    description: str
    external_ref: str
    clearing_amount: Decimal
    splits: tuple[PlannedSplit, ...]
    source_row_ids: tuple[str, ...]
    dedupe_aliases: tuple[str, ...] = ()
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
    marketplace: str
    marketplace_account_key: str | None
    marketplace_account_label: str | None


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
    marketplace_account_labels: tuple[str, ...] = ()


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
class BankTransferResult:
    bank_dedupe_key: str
    bank_txn_id: str
    bank_description: str
    bank_date: date
    bank_amount: Decimal
    bank_account_guid: str | None
    bank_account_label: str
    status: str
    match_source: str
    counterpart_dedupe_key: str | None
    counterpart_txn_id: str | None
    counterpart_account_guid: str | None
    counterpart_account_label: str | None


@dataclass(frozen=True)
class TransferAnchor:
    anchor_dedupe_key: str
    bank_txn_id: str
    txn_date: date
    amount: Decimal
    source_account_guid: str
    source_account_label: str
    destination_account_guid: str
    destination_account_label: str
    description: str
    external_ref: str
    anchor_source: str


@dataclass(frozen=True)
class TransferAnchorResolution:
    anchor_dedupe_key: str
    counterpart_dedupe_key: str


@dataclass(frozen=True)
class CarryoverCandidate:
    candidate_key: str
    candidate_type: str
    source_scope: str
    txn_date: date
    amount: Decimal
    description: str
    payload: Mapping[str, Any]
    transaction: PlannedTransaction


@dataclass(frozen=True)
class PlanResult:
    transactions: tuple[PlannedTransactionStatus, ...]
    warnings: tuple[str, ...]
    marketplace_mapping_keys: Mapping[str, tuple[str, ...]]
    bank_match_results: tuple[BankMatchResult, ...]
    bank_transfer_results: tuple[BankTransferResult, ...]
    bank_category_results: tuple[BankCategoryResult, ...]
    transfer_anchor_candidates: tuple[TransferAnchor, ...] = ()
    matched_transfer_anchor_resolutions: tuple[TransferAnchorResolution, ...] = ()
    matched_carryover_candidate_keys: tuple[str, ...] = ()
    pending_carryover_count: int = 0
