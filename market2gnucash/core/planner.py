from __future__ import annotations

from datetime import date

from market2gnucash.core.dedupe_store import DedupeStore
from market2gnucash.core.models import MappingConfig, PlanResult, PlannedTransactionStatus
from market2gnucash.core.parsers import parse_ebay_report, parse_etsy_inputs
from market2gnucash.core.rules import build_ebay_transactions, build_etsy_transactions


def _status_for_transaction(warnings: tuple[str, ...], is_duplicate: bool) -> tuple[str, str]:
    if is_duplicate:
        return "duplicate", "Already imported"

    for warning in warnings:
        if warning.startswith("MISSING_ACCOUNT"):
            return "blocked", "Missing required account selection"
        if warning.startswith("UNMAPPED"):
            return "blocked", "Unmapped fee key"
        if warning.startswith("UNBALANCED"):
            return "blocked", "Transaction is unbalanced"

    return "ready", "Ready"


def build_plan(
    *,
    book_id: str,
    dedupe_store: DedupeStore,
    mapping: MappingConfig,
    etsy_statement_path: str | None,
    etsy_sold_orders_path: str | None,
    ebay_report_path: str | None,
    start_date: date | None,
    end_date: date | None,
) -> PlanResult:
    all_transactions = []
    all_warnings: list[str] = []
    etsy_mapping_keys: tuple[str, ...] = ()
    ebay_fee_columns: tuple[str, ...] = ()

    if etsy_statement_path or etsy_sold_orders_path:
        if not (etsy_statement_path and etsy_sold_orders_path):
            raise ValueError("Etsy import requires both statement CSV and SoldOrders CSV")

        etsy_data = parse_etsy_inputs(
            statement_path=etsy_statement_path,
            sold_orders_path=etsy_sold_orders_path,
            start_date=start_date,
            end_date=end_date,
        )
        etsy_txns, etsy_warnings, etsy_mapping_keys = build_etsy_transactions(etsy_data, mapping)
        all_transactions.extend(etsy_txns)
        all_warnings.extend(etsy_warnings)

    if ebay_report_path:
        ebay_data = parse_ebay_report(ebay_report_path, start_date=start_date, end_date=end_date)
        ebay_txns, ebay_warnings, ebay_fee_columns = build_ebay_transactions(ebay_data, mapping)
        all_transactions.extend(ebay_txns)
        all_warnings.extend(ebay_warnings)

    dedupe_keys = [txn.dedupe_key for txn in all_transactions]
    existing = dedupe_store.existing_keys(book_id, dedupe_keys)

    statuses: list[PlannedTransactionStatus] = []
    for txn in sorted(all_transactions, key=lambda value: (value.date, value.marketplace, value.txn_kind, value.txn_id)):
        status, reason = _status_for_transaction(txn.warnings, txn.dedupe_key in existing)
        statuses.append(
            PlannedTransactionStatus(transaction=txn, status=status, status_reason=reason)
        )
        for warning in txn.warnings:
            all_warnings.append(f"{txn.marketplace}:{txn.txn_kind}:{txn.txn_id}: {warning}")

    return PlanResult(
        transactions=tuple(statuses),
        warnings=tuple(all_warnings),
        etsy_mapping_keys=tuple(sorted(set(etsy_mapping_keys))),
        ebay_fee_columns=tuple(sorted(set(ebay_fee_columns))),
    )
