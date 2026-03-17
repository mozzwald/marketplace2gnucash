from __future__ import annotations

from datetime import date

from market2gnucash.core.dedupe_store import DedupeStore
from market2gnucash.core.models import (
    BankImportSpec,
    BankStatementData,
    BankCsvProfile,
    MappingConfig,
    PlanResult,
    PlannedTransactionStatus,
)
from market2gnucash.core.parsers import (
    parse_bank_statement_files,
    parse_ebay_report,
    parse_etsy_inputs,
)
from market2gnucash.core.rules import (
    build_bank_transactions,
    build_ebay_transactions,
    build_ebay_payout_match_candidates,
    build_etsy_deposit_match_candidates,
    build_etsy_payment_match_candidates,
    build_etsy_transactions,
)


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
        if warning.startswith("MATCH_AMBIGUOUS"):
            return "blocked", "Ambiguous bank/card match"
        if warning.startswith("MATCH_OVERRIDE_INVALID"):
            return "blocked", "Invalid bank/card match override"

    return "ready", "Ready"


def build_plan(
    *,
    book_id: str,
    dedupe_store: DedupeStore,
    mapping: MappingConfig,
    etsy_statement_path: str | None,
    etsy_sold_orders_path: str | None,
    ebay_report_path: str | None,
    bank_imports: list[dict[str, object]] | tuple[dict[str, object], ...] | None,
    start_date: date | None,
    end_date: date | None,
) -> PlanResult:
    all_transactions = []
    all_warnings: list[str] = []
    etsy_mapping_keys: tuple[str, ...] = ()
    ebay_fee_columns: tuple[str, ...] = ()
    bank_match_results = ()
    bank_category_results = ()
    marketplace_payout_candidates = ()

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
        marketplace_payout_candidates = tuple(
            [
                *build_etsy_deposit_match_candidates(etsy_data, mapping),
                *build_etsy_payment_match_candidates(etsy_data, mapping),
            ]
        )
        all_transactions.extend(etsy_txns)
        all_warnings.extend(etsy_warnings)

    if ebay_report_path:
        ebay_data = parse_ebay_report(ebay_report_path, start_date=start_date, end_date=end_date)
        ebay_txns, ebay_warnings, ebay_fee_columns = build_ebay_transactions(ebay_data, mapping)
        marketplace_payout_candidates = tuple(
            [*marketplace_payout_candidates, *build_ebay_payout_match_candidates(ebay_data, mapping)]
        )
        all_transactions.extend(ebay_txns)
        all_warnings.extend(ebay_warnings)

    if bank_imports:
        parsed_bank_imports: list[tuple[BankImportSpec, tuple[BankStatementData, ...]]] = []
        bank_row_count = 0
        for raw_import in bank_imports:
            account_guid = raw_import.get("account_guid")
            statement_paths = tuple(
                path for path in raw_import.get("statement_paths", []) if isinstance(path, str) and path
            )
            csv_profiles: dict[str, BankCsvProfile | dict[str, object]] = {}
            raw_profiles = raw_import.get("csv_profiles", {})
            if isinstance(raw_profiles, dict):
                for path, profile in raw_profiles.items():
                    if isinstance(path, str):
                        csv_profiles[path] = profile
            if not statement_paths and not account_guid:
                continue
            if not statement_paths:
                all_warnings.append(
                    f"INFO: Bank/Card import bundle for account {account_guid or '(unselected)'} has no statement files"
                )
            spec = BankImportSpec(
                account_guid=account_guid if isinstance(account_guid, str) else None,
                statement_paths=statement_paths,
                csv_profiles=csv_profiles,
            )
            statements = parse_bank_statement_files(
                statement_paths,
                start_date=start_date,
                end_date=end_date,
                csv_profiles=csv_profiles,
            )
            bank_row_count += sum(len(statement.rows) for statement in statements)
            parsed_bank_imports.append((spec, statements))

        bank_txns, bank_warnings, bank_match_results, bank_category_results = build_bank_transactions(
            tuple(parsed_bank_imports),
            mapping,
            tuple(txn for txn in all_transactions if txn.marketplace in {"etsy", "ebay"}),
            marketplace_payout_candidates=marketplace_payout_candidates,
        )
        all_transactions.extend(bank_txns)
        all_warnings.extend(bank_warnings)
        all_warnings.append(
            f"INFO: Parsed {len(parsed_bank_imports)} bank/card import bundle(s) with {bank_row_count} normalized row(s)"
        )
        for spec, statements in parsed_bank_imports:
            all_warnings.append(
                f"INFO: Bank/Card import bundle for account {spec.account_guid or '(unselected)'} has {len(statements)} statement file(s)"
            )
            for statement in statements:
                account_label = statement.account_id or statement.account_name or "unknown-account"
                all_warnings.append(
                    f"INFO: Bank/Card parsed {len(statement.rows)} row(s) from {statement.source_path} ({statement.source_format}, {account_label})"
                )

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
        bank_match_results=bank_match_results,
        bank_category_results=bank_category_results,
    )
