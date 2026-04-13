from __future__ import annotations

from dataclasses import replace
from datetime import date

from market2gnucash.core.carryover_store import CarryoverStore
from market2gnucash.core.dedupe_store import DedupeStore
from market2gnucash.core.models import (
    AccountRecord,
    BankImportSpec,
    BankStatementData,
    BankCsvProfile,
    CarryoverCandidate,
    MappingConfig,
    MarketplaceImportSpec,
    PlanResult,
    PlannedTransaction,
    PlannedTransactionStatus,
    TransferAnchor,
    TransferAnchorResolution,
)
from market2gnucash.core.parsers import (
    parse_bank_statement_files,
    parse_ebay_report,
    parse_etsy_inputs,
)
from market2gnucash.core.rules import (
    build_bank_transactions,
    build_ebay_charge_match_candidates,
    build_ebay_transactions,
    build_ebay_payout_match_candidates,
    build_etsy_deposit_match_candidates,
    build_etsy_payment_match_candidates,
    build_etsy_transactions,
)


def _status_for_transaction(warnings: tuple[str, ...], is_duplicate: bool) -> tuple[str, str]:
    for warning in warnings:
        if warning.startswith("IMPORTED_TRANSFER_COUNTERPART"):
            return "counterpart", "Matched to previously imported transfer"
        if warning.startswith("TRANSFER_COUNTERPART"):
            return "deferred", "Matched as internal transfer counterpart"
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
        if warning.startswith("TRANSFER_AMBIGUOUS"):
            return "blocked", "Ambiguous bank/card transfer match"
        if warning.startswith("TRANSFER_OVERRIDE_INVALID"):
            return "blocked", "Invalid bank/card transfer override"

    if is_duplicate:
        return "duplicate", "Already imported"

    return "ready", "Ready"


def _carryover_key_for_candidate(transaction: PlannedTransaction) -> str:
    parts = transaction.dedupe_key.split(":")
    if len(parts) >= 2:
        return ":".join([parts[0], parts[1], "carry", *parts[2:]])
    return f"carry:{transaction.dedupe_key}"


def _is_carryover_key(dedupe_key: str) -> bool:
    return ":carry:" in dedupe_key or dedupe_key.startswith("carry:")


def _carryover_candidate_from_transaction(transaction: PlannedTransaction) -> CarryoverCandidate:
    candidate_key = _carryover_key_for_candidate(transaction)
    carried_transaction = replace(transaction, dedupe_key=candidate_key)
    candidate_type = transaction.txn_kind.replace("_match", "")
    source_scope = transaction.marketplace_account_key or transaction.marketplace
    payload = {
        "transaction": CarryoverStore.serialize_transaction(carried_transaction),
    }
    return CarryoverCandidate(
        candidate_key=candidate_key,
        candidate_type=candidate_type,
        source_scope=source_scope,
        txn_date=transaction.date,
        amount=transaction.clearing_amount,
        description=transaction.description,
        payload=payload,
        transaction=carried_transaction,
    )


def build_plan(
    *,
    book_id: str,
    dedupe_store: DedupeStore,
    carryover_store: CarryoverStore,
    mapping: MappingConfig,
    marketplace_imports: list[dict[str, object]] | tuple[dict[str, object], ...] | None,
    bank_imports: list[dict[str, object]] | tuple[dict[str, object], ...] | None,
    start_date: date | None,
    end_date: date | None,
) -> PlanResult:
    all_transactions = []
    all_warnings: list[str] = []
    marketplace_mapping_keys: dict[str, tuple[str, ...]] = {}
    bank_match_results = ()
    bank_transfer_results = ()
    bank_category_results = ()
    bank_txns = ()
    marketplace_payout_candidates = ()
    transfer_anchor_candidates: tuple[TransferAnchor, ...] = ()
    matched_transfer_anchor_resolutions: tuple[TransferAnchorResolution, ...] = ()
    matched_carryover_candidate_keys: tuple[str, ...] = ()

    normalized_marketplace_imports: list[MarketplaceImportSpec] = []
    for raw_import in marketplace_imports or ():
        if not isinstance(raw_import, dict):
            continue
        marketplace = raw_import.get("marketplace")
        account_key = raw_import.get("account_key")
        account_label = raw_import.get("account_label")
        import_id = raw_import.get("import_id")
        if not all(isinstance(value, str) and value for value in (marketplace, account_key, account_label, import_id)):
            continue
        normalized_marketplace_imports.append(
            MarketplaceImportSpec(
                import_id=import_id,
                marketplace=marketplace,
                account_key=account_key,
                account_label=account_label,
                etsy_statement_path=raw_import.get("etsy_statement_path") if isinstance(raw_import.get("etsy_statement_path"), str) else None,
                etsy_sold_orders_path=raw_import.get("etsy_sold_orders_path") if isinstance(raw_import.get("etsy_sold_orders_path"), str) else None,
                ebay_report_path=raw_import.get("ebay_report_path") if isinstance(raw_import.get("ebay_report_path"), str) else None,
            )
        )

    for marketplace_import in normalized_marketplace_imports:
        if marketplace_import.marketplace == "etsy":
            if not (marketplace_import.etsy_statement_path and marketplace_import.etsy_sold_orders_path):
                raise ValueError(
                    f"Etsy import '{marketplace_import.account_label}' requires both statement CSV and SoldOrders CSV"
                )
            etsy_data = parse_etsy_inputs(
                statement_path=marketplace_import.etsy_statement_path,
                sold_orders_path=marketplace_import.etsy_sold_orders_path,
                start_date=start_date,
                end_date=end_date,
            )
            etsy_txns, etsy_warnings, etsy_mapping_keys = build_etsy_transactions(
                etsy_data,
                mapping,
                account_key=marketplace_import.account_key,
                account_label=marketplace_import.account_label,
            )
            marketplace_mapping_keys[marketplace_import.account_key] = tuple(sorted(set(etsy_mapping_keys)))
            marketplace_payout_candidates = tuple(
                [
                    *marketplace_payout_candidates,
                    *build_etsy_deposit_match_candidates(
                        etsy_data,
                        mapping,
                        account_key=marketplace_import.account_key,
                        account_label=marketplace_import.account_label,
                    ),
                    *build_etsy_payment_match_candidates(
                        etsy_data,
                        mapping,
                        account_key=marketplace_import.account_key,
                        account_label=marketplace_import.account_label,
                    ),
                ]
            )
            all_transactions.extend(etsy_txns)
            all_warnings.extend(
                f"[{marketplace_import.account_label}] {warning}" for warning in etsy_warnings
            )
        elif marketplace_import.marketplace == "ebay":
            if not marketplace_import.ebay_report_path:
                raise ValueError(f"eBay import '{marketplace_import.account_label}' requires a transaction report CSV")
            ebay_data = parse_ebay_report(
                marketplace_import.ebay_report_path,
                start_date=start_date,
                end_date=end_date,
            )
            ebay_txns, ebay_warnings, ebay_mapping_keys = build_ebay_transactions(
                ebay_data,
                mapping,
                account_key=marketplace_import.account_key,
                account_label=marketplace_import.account_label,
            )
            marketplace_mapping_keys[marketplace_import.account_key] = tuple(sorted(set(ebay_mapping_keys)))
            marketplace_payout_candidates = tuple(
                [
                    *marketplace_payout_candidates,
                    *build_ebay_payout_match_candidates(
                        ebay_data,
                        mapping,
                        account_key=marketplace_import.account_key,
                        account_label=marketplace_import.account_label,
                    ),
                    *build_ebay_charge_match_candidates(
                        ebay_data,
                        mapping,
                        account_key=marketplace_import.account_key,
                        account_label=marketplace_import.account_label,
                    ),
                ]
            )
            all_transactions.extend(ebay_txns)
            all_warnings.extend(
                f"[{marketplace_import.account_label}] {warning}" for warning in ebay_warnings
            )

    current_carryover_candidates = tuple(
        _carryover_candidate_from_transaction(transaction)
        for transaction in marketplace_payout_candidates
    )
    current_carryover_by_key = {
        candidate.candidate_key: candidate for candidate in current_carryover_candidates
    }
    current_candidate_resolution_keys = {
        transaction.dedupe_key: candidate.candidate_key
        for transaction, candidate in zip(marketplace_payout_candidates, current_carryover_candidates)
    }
    pending_carryover_candidates = tuple(
        candidate
        for candidate in carryover_store.list_pending_candidates(book_id)
        if candidate.candidate_key not in current_carryover_by_key
    )
    combined_marketplace_payout_candidates = tuple(
        [*marketplace_payout_candidates, *(candidate.transaction for candidate in pending_carryover_candidates)]
    )

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

        (
            bank_txns,
            bank_warnings,
            bank_match_results,
            bank_transfer_results,
            bank_category_results,
        ) = build_bank_transactions(
            tuple(parsed_bank_imports),
            mapping,
            tuple(txn for txn in all_transactions if txn.marketplace in {"etsy", "ebay"}),
            marketplace_payout_candidates=combined_marketplace_payout_candidates,
            pending_transfer_anchors=dedupe_store.pending_transfer_anchors(book_id),
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

    carryover_resolution_keys: set[str] = set()
    matched_marketplace_candidate_ids: set[str] = set()
    for result in bank_match_results:
        if result.status != "matched":
            continue
        for matched_id in result.matched_transaction_ids:
            matched_marketplace_candidate_ids.add(matched_id)
            if _is_carryover_key(matched_id):
                carryover_resolution_keys.add(matched_id)
            elif matched_id in current_candidate_resolution_keys:
                carryover_resolution_keys.add(current_candidate_resolution_keys[matched_id])

    unresolved_current_candidates = [
        candidate
        for candidate in current_carryover_candidates
        if candidate.candidate_key not in carryover_resolution_keys
        and candidate.transaction.dedupe_key not in matched_marketplace_candidate_ids
    ]
    carryover_store.upsert_pending_candidates(book_id, unresolved_current_candidates)
    matched_carryover_candidate_keys = tuple(sorted(carryover_resolution_keys))
    pending_carryover_count = carryover_store.pending_count(book_id)
    if pending_carryover_candidates:
        all_warnings.append(
            f"INFO: Loaded {len(pending_carryover_candidates)} pending marketplace carryover candidate(s)"
        )
    if unresolved_current_candidates:
        all_warnings.append(
            f"INFO: Saved {len(unresolved_current_candidates)} unresolved marketplace carryover candidate(s)"
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

    if bank_txns:
        category_by_key = {result.bank_dedupe_key: result for result in bank_category_results}
        transfer_by_key = {result.bank_dedupe_key: result for result in bank_transfer_results}
        status_by_key = {
            status_row.transaction.dedupe_key: status_row
            for status_row in statuses
            if status_row.transaction.marketplace == "bank"
        }
        candidates: list[TransferAnchor] = []
        resolutions: list[TransferAnchorResolution] = []
        for dedupe_key, status_row in status_by_key.items():
            transfer_result = transfer_by_key.get(dedupe_key)
            if transfer_result and transfer_result.status == "imported_counterpart" and transfer_result.counterpart_dedupe_key:
                resolutions.append(
                    TransferAnchorResolution(
                        anchor_dedupe_key=transfer_result.counterpart_dedupe_key,
                        counterpart_dedupe_key=dedupe_key,
                    )
                )
            if status_row.status != "ready":
                continue
            if transfer_result and transfer_result.status in {"matched", "manual", "counterpart", "imported_counterpart"}:
                continue
            category_result = category_by_key.get(dedupe_key)
            if category_result is None or not category_result.mapped_account_guid:
                continue
            source_split = next(
                (split for split in status_row.transaction.splits if split.mapping_key == "bank:account" and split.account_guid),
                None,
            )
            if source_split is None or source_split.account_guid == category_result.mapped_account_guid:
                continue
            candidates.append(
                TransferAnchor(
                    anchor_dedupe_key=dedupe_key,
                    bank_txn_id=status_row.transaction.txn_id,
                    txn_date=status_row.transaction.date,
                    amount=status_row.transaction.clearing_amount,
                    source_account_guid=source_split.account_guid,
                    source_account_label="",
                    destination_account_guid=category_result.mapped_account_guid,
                    destination_account_label="",
                    description=status_row.transaction.description,
                    external_ref=status_row.transaction.external_ref,
                    anchor_source="merchant_default" if category_result.mapping_source == "merchant" else "transaction",
                )
            )
        transfer_anchor_candidates = tuple(candidates)
        matched_transfer_anchor_resolutions = tuple(resolutions)

    return PlanResult(
        transactions=tuple(statuses),
        warnings=tuple(all_warnings),
        marketplace_mapping_keys=marketplace_mapping_keys,
        bank_match_results=bank_match_results,
        bank_transfer_results=bank_transfer_results,
        bank_category_results=bank_category_results,
        transfer_anchor_candidates=transfer_anchor_candidates,
        matched_transfer_anchor_resolutions=matched_transfer_anchor_resolutions,
        matched_carryover_candidate_keys=matched_carryover_candidate_keys,
        pending_carryover_count=pending_carryover_count,
    )
