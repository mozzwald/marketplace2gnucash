from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

from market2gnucash.core.decimal_utils import ZERO, parse_money
from market2gnucash.core.models import (
    BankCategoryResult,
    BankMatchResult,
    BankMatchTarget,
    BankImportSpec,
    BankStatementData,
    EbayInputData,
    EbayReportRow,
    EtsyInputData,
    EtsyStatementRow,
    MappingConfig,
    MarketplaceAccountMapping,
    PlannedSplit,
    PlannedTransaction,
)

_BANK_NOISE_TOKENS = {
    "purchase",
    "payment",
    "payments",
    "debit",
    "credit",
    "pending",
    "posted",
    "card",
    "visa",
    "mastercard",
    "auth",
    "authorization",
    "trans",
    "trn",
    "nte",
    "rmr",
    "dda",
    "pos",
    "checkcard",
    "withdrawal",
    "deposit",
}
_TITLE_MONEY_RE = re.compile(r"(\$?\d[\d,]*\.\d{2})")


def etsy_mapping_key(row: EtsyStatementRow) -> str:
    return f"etsy:{row.row_type}:{row.title}"


def etsy_mapping_key_candidates(row: EtsyStatementRow) -> tuple[str, ...]:
    exact = etsy_mapping_key(row)
    if row.row_type == "Fee" and row.title.startswith("Transaction fee:") and row.title != "Transaction fee: Shipping":
        return (exact, "etsy:Fee:Transaction fee:*")
    return (exact,)


def ebay_mapping_key(column_name: str) -> str:
    return f"ebay:fee_col:{column_name}"


def bank_merchant_key(description: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", description.lower())
    filtered: list[str] = []
    for token in tokens:
        if token in _BANK_NOISE_TOKENS:
            continue
        if token.isdigit():
            continue
        if any(char.isdigit() for char in token):
            letters_only = "".join(char for char in token if char.isalpha())
            if len(letters_only) < 3:
                continue
            token = letters_only
        if len(token) < 2:
            continue
        filtered.append(token)
    if not filtered:
        return "uncategorized"
    return " ".join(filtered[:6])


def _extract_money_from_text(text: str) -> Decimal | None:
    match = _TITLE_MONEY_RE.search(text)
    if not match:
        return None
    return parse_money(match.group(1))


def _split_sum(splits: list[PlannedSplit]) -> Decimal:
    total = ZERO
    for split in splits:
        total += split.amount
    return total


def _finalize_transaction(
    *,
    dedupe_key: str,
    marketplace: str,
    marketplace_account_key: str | None,
    marketplace_account_label: str | None,
    txn_kind: str,
    txn_id: str,
    txn_date: date,
    description: str,
    external_ref: str,
    clearing_amount: Decimal,
    splits: list[PlannedSplit],
    source_row_ids: list[str],
    warnings: list[str],
) -> PlannedTransaction:
    split_total = _split_sum(splits)
    if split_total != ZERO:
        warnings.append(f"UNBALANCED: transaction sums to {split_total}")
    return PlannedTransaction(
        dedupe_key=dedupe_key,
        marketplace=marketplace,
        marketplace_account_key=marketplace_account_key,
        marketplace_account_label=marketplace_account_label,
        txn_kind=txn_kind,
        txn_id=txn_id,
        date=txn_date,
        description=description,
        external_ref=external_ref,
        clearing_amount=clearing_amount,
        splits=tuple(splits),
        source_row_ids=tuple(source_row_ids),
        warnings=tuple(warnings),
    )


def _has_blocking_warnings(transaction: PlannedTransaction) -> bool:
    return any(
        warning.startswith(("MISSING_ACCOUNT", "UNMAPPED", "UNBALANCED", "MATCH_AMBIGUOUS"))
        for warning in transaction.warnings
    )


def _is_etsy_refund_adjustment(row: EtsyStatementRow) -> bool:
    net = row.net or ZERO
    if row.row_type == "Fee" and net > ZERO:
        return True
    if row.row_type == "Tax" and "refund" in row.title.lower():
        return True
    return False


def _is_etsy_sale_related(row: EtsyStatementRow) -> bool:
    if row.row_type not in {"Sale", "Fee", "Tax"}:
        return False
    if row.row_type == "Fee" and row.title == "Listing fee":
        return False
    if _is_etsy_refund_adjustment(row):
        return False
    return True


def marketplace_mapping(
    mapping: MappingConfig,
    *,
    account_key: str,
    marketplace: str,
    account_label: str,
) -> MarketplaceAccountMapping:
    configured = mapping.marketplace_accounts.get(account_key)
    if configured is not None:
        return configured
    return MarketplaceAccountMapping(marketplace=marketplace, account_label=account_label)


def _lookup_etsy_fee_account_for_marketplace(
    account_mapping: MarketplaceAccountMapping,
    row: EtsyStatementRow,
) -> tuple[str | None, str | None]:
    for key in etsy_mapping_key_candidates(row):
        account_guid = account_mapping.fee_accounts.get(key)
        if account_guid:
            return key, account_guid
    return None, None


def build_etsy_transactions(
    data: EtsyInputData,
    mapping: MappingConfig,
    *,
    account_key: str,
    account_label: str,
) -> tuple[tuple[PlannedTransaction, ...], tuple[str, ...], tuple[str, ...]]:
    transactions: list[PlannedTransaction] = []
    warnings: list[str] = []
    account_mapping = marketplace_mapping(
        mapping,
        account_key=account_key,
        marketplace="etsy",
        account_label=account_label,
    )

    statement_rows = data.statement_rows
    sold_by_order = {row.order_id: row for row in data.sold_orders}
    rows_by_order: dict[str, list[EtsyStatementRow]] = defaultdict(list)
    mapping_keys: set[str] = set()

    for row in statement_rows:
        if row.row_type == "Fee":
            mapping_keys.add(etsy_mapping_key(row))
            if row.title.startswith("Transaction fee:") and row.title != "Transaction fee: Shipping":
                mapping_keys.add("etsy:Fee:Transaction fee:*")
        if row.order_id:
            rows_by_order[row.order_id].append(row)

    consumed_rows: set[str] = set()

    for order_id, sold in sold_by_order.items():
        order_rows = rows_by_order.get(order_id, [])
        sale_rows = [row for row in order_rows if _is_etsy_sale_related(row)]
        sale_present = any(row.row_type == "Sale" for row in sale_rows)

        if not sale_present:
            continue

        sale_warnings: list[str] = []
        sale_source_ids = [row.row_id for row in sale_rows]
        sale_date = sold.sale_date

        clearing_amount = ZERO
        statement_tax = ZERO
        fee_rows: list[EtsyStatementRow] = []
        for row in sale_rows:
            net = row.net or ZERO
            clearing_amount += net
            if row.row_type == "Tax":
                statement_tax += abs(net)
            elif row.row_type == "Fee":
                fee_rows.append(row)

        income_base = sold.order_total - statement_tax

        splits: list[PlannedSplit] = []
        if account_mapping.clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.clearing_guid,
                    amount=clearing_amount,
                    memo=f"Order #{order_id} net proceeds",
                )
            )
        else:
            sale_warnings.append("MISSING_ACCOUNT: Etsy clearing account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=clearing_amount,
                    memo=f"Order #{order_id} net proceeds",
                )
            )

        if account_mapping.income_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.income_guid,
                    amount=-income_base,
                    memo=f"Order #{order_id} income base",
                )
            )
        else:
            sale_warnings.append("MISSING_ACCOUNT: Etsy income account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=-income_base,
                    memo=f"Order #{order_id} income base",
                )
            )

        for row in fee_rows:
            key, account_guid = _lookup_etsy_fee_account_for_marketplace(account_mapping, row)
            fee_amount = -(row.net or ZERO)
            if account_guid is None:
                sale_warnings.append(f"UNMAPPED: No Etsy mapping for {etsy_mapping_key(row)}")

            splits.append(
                PlannedSplit(
                    account_guid=account_guid,
                    amount=fee_amount,
                    memo=row.title,
                    mapping_key=key or etsy_mapping_key(row),
                )
            )

        if income_base < ZERO:
            sale_warnings.append(
                f"MISMATCH: Negative Etsy income base for order {order_id} (OrderTotal {sold.order_total}, tax {statement_tax})"
            )

        transactions.append(
            _finalize_transaction(
                dedupe_key=f"etsy:sale:{account_key}:{order_id}",
                marketplace="etsy",
                marketplace_account_key=account_key,
                marketplace_account_label=account_label,
                txn_kind="sale",
                txn_id=order_id,
                txn_date=sale_date,
                description=f"Etsy Sale Order #{order_id}",
                external_ref=order_id,
                clearing_amount=clearing_amount,
                splits=splits,
                source_row_ids=sale_source_ids,
                warnings=sale_warnings,
            )
        )
        consumed_rows.update(sale_source_ids)

    for row in statement_rows:
        if row.row_type == "Fee" and row.title == "Listing fee":
            listing_warnings: list[str] = []
            mapping_key = etsy_mapping_key(row)
            account_guid = account_mapping.fee_accounts.get(mapping_key)
            if account_guid is None:
                listing_warnings.append(f"UNMAPPED: No Etsy mapping for {mapping_key}")

            clearing_amount = row.net or ZERO
            splits: list[PlannedSplit] = []
            if account_mapping.clearing_guid:
                splits.append(
                    PlannedSplit(
                        account_guid=account_mapping.clearing_guid,
                        amount=clearing_amount,
                        memo="Listing fee clearing",
                    )
                )
            else:
                listing_warnings.append("MISSING_ACCOUNT: Etsy clearing account is not selected")
                splits.append(
                    PlannedSplit(
                        account_guid=None,
                        amount=clearing_amount,
                        memo="Listing fee clearing",
                    )
                )

            splits.append(
                PlannedSplit(
                    account_guid=account_guid,
                    amount=-(row.net or ZERO),
                    memo="Listing fee expense",
                    mapping_key=mapping_key,
                )
            )

            transactions.append(
                _finalize_transaction(
                    dedupe_key=f"etsy:listing_fee:{account_key}:{row.row_id}",
                    marketplace="etsy",
                    marketplace_account_key=account_key,
                    marketplace_account_label=account_label,
                    txn_kind="listing_fee",
                    txn_id=row.listing_id or row.row_id,
                    txn_date=row.date,
                    description=f"Etsy Listing Fee {row.listing_id or ''}".strip(),
                    external_ref=row.row_id,
                    clearing_amount=clearing_amount,
                    splits=splits,
                    source_row_ids=[row.row_id],
                    warnings=listing_warnings,
                )
            )
            consumed_rows.add(row.row_id)

    adjustments_by_order_date: dict[tuple[str, date], list[EtsyStatementRow]] = defaultdict(list)
    for row in statement_rows:
        if row.order_id and _is_etsy_refund_adjustment(row):
            adjustments_by_order_date[(row.order_id, row.date)].append(row)

    used_adjustments: set[str] = set()
    for row in statement_rows:
        if row.row_type != "Refund":
            continue

        refund_warnings: list[str] = []
        order_id = row.order_id or "unknown"
        related_adjustments = [
            adj
            for adj in adjustments_by_order_date.get((order_id, row.date), [])
            if adj.row_id not in used_adjustments
        ]

        source_rows = [row, *related_adjustments]
        source_ids = [source_row.row_id for source_row in source_rows]

        clearing_amount = ZERO
        for source_row in source_rows:
            clearing_amount += source_row.net or ZERO

        splits: list[PlannedSplit] = []
        if account_mapping.clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.clearing_guid,
                    amount=clearing_amount,
                    memo=f"Refund Order #{order_id} net",
                )
            )
        else:
            refund_warnings.append("MISSING_ACCOUNT: Etsy clearing account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=clearing_amount,
                    memo=f"Refund Order #{order_id} net",
                )
            )

        fee_adjustment_total = ZERO
        for adj in related_adjustments:
            if adj.row_type != "Fee":
                continue
            key, account_guid = _lookup_etsy_fee_account_for_marketplace(account_mapping, adj)
            fee_amount = -(adj.net or ZERO)
            fee_adjustment_total += fee_amount
            if account_guid is None:
                refund_warnings.append(f"UNMAPPED: No Etsy mapping for {etsy_mapping_key(adj)}")
            splits.append(
                PlannedSplit(
                    account_guid=account_guid,
                    amount=fee_amount,
                    memo=adj.title,
                    mapping_key=key or etsy_mapping_key(adj),
                )
            )

        refunds_amount = -(clearing_amount + fee_adjustment_total)
        if account_mapping.refunds_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.refunds_guid,
                    amount=refunds_amount,
                    memo=f"Refund expense Order #{order_id}",
                    mapping_key="etsy:refunds",
                )
            )
        else:
            refund_warnings.append("MISSING_ACCOUNT: Etsy refunds account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=refunds_amount,
                    memo=f"Refund expense Order #{order_id}",
                    mapping_key="etsy:refunds",
                )
            )

        transactions.append(
            _finalize_transaction(
                dedupe_key=f"etsy:refund:{account_key}:{row.row_id}",
                marketplace="etsy",
                marketplace_account_key=account_key,
                marketplace_account_label=account_label,
                txn_kind="refund",
                txn_id=order_id,
                txn_date=row.date,
                description=f"Etsy Refund Order #{order_id}",
                external_ref=row.row_id,
                clearing_amount=clearing_amount,
                splits=splits,
                source_row_ids=source_ids,
                warnings=refund_warnings,
            )
        )
        consumed_rows.update(source_ids)
        used_adjustments.update(adj.row_id for adj in related_adjustments)

    for sold_order in data.sold_orders:
        if sold_order.order_id not in rows_by_order:
            warnings.append(f"MISMATCH: SoldOrders has order {sold_order.order_id} not found in Etsy statement")

    for row in statement_rows:
        net = row.net or ZERO
        if row.row_id in consumed_rows:
            continue
        if row.row_type == "Deposit":
            continue
        if net == ZERO:
            continue
        warnings.append(
            f"UNMATCHED_ROW: Etsy statement row {row.row_number} ({row.row_type} / {row.title}) was not mapped to a transaction"
        )

    transactions.sort(key=lambda txn: (txn.date, txn.marketplace, txn.txn_kind, txn.txn_id))
    return tuple(transactions), tuple(warnings), tuple(sorted(mapping_keys))


def build_etsy_deposit_match_candidates(
    data: EtsyInputData,
    mapping: MappingConfig,
    *,
    account_key: str,
    account_label: str,
) -> tuple[PlannedTransaction, ...]:
    account_mapping = marketplace_mapping(
        mapping,
        account_key=account_key,
        marketplace="etsy",
        account_label=account_label,
    )
    if not account_mapping.clearing_guid:
        return ()

    candidates: list[PlannedTransaction] = []
    for row in data.statement_rows:
        if row.row_type != "Deposit":
            continue
        amount = row.net or row.amount or _extract_money_from_text(row.title) or ZERO
        if amount == ZERO:
            continue
        description = row.title or f"Etsy Deposit {row.row_id}"
        external_ref = row.order_id or row.row_id
        candidates.append(
            PlannedTransaction(
                dedupe_key=f"etsy:deposit:{account_key}:{row.row_id}",
                marketplace="etsy",
                marketplace_account_key=account_key,
                marketplace_account_label=account_label,
                txn_kind="deposit_match",
                txn_id=row.row_id,
                date=row.date,
                description=description,
                external_ref=external_ref,
                clearing_amount=amount,
                splits=(
                    PlannedSplit(
                        account_guid=account_mapping.clearing_guid,
                        amount=amount,
                        memo=f"Etsy deposit {external_ref}",
                        mapping_key="etsy:deposit-match",
                    ),
                    PlannedSplit(
                        account_guid=None,
                        amount=-amount,
                        memo=f"Etsy deposit offset {external_ref}",
                        mapping_key="etsy:deposit-match-offset",
                    ),
                ),
                source_row_ids=(row.row_id,),
                warnings=(),
            )
        )

    candidates.sort(key=lambda txn: (txn.date, txn.txn_id))
    return tuple(candidates)


def build_etsy_payment_match_candidates(
    data: EtsyInputData,
    mapping: MappingConfig,
    *,
    account_key: str,
    account_label: str,
) -> tuple[PlannedTransaction, ...]:
    account_mapping = marketplace_mapping(
        mapping,
        account_key=account_key,
        marketplace="etsy",
        account_label=account_label,
    )
    if not account_mapping.clearing_guid:
        return ()

    candidates: list[PlannedTransaction] = []
    for row in data.statement_rows:
        if row.row_type != "Payment":
            continue
        raw_amount = row.net or row.fees_taxes or row.amount or _extract_money_from_text(row.title) or ZERO
        amount = -abs(raw_amount)
        if amount == ZERO:
            continue
        external_ref = row.info or row.row_id
        description = row.title or f"Etsy Payment {external_ref}"
        candidates.append(
            PlannedTransaction(
                dedupe_key=f"etsy:payment:{account_key}:{row.row_id}",
                marketplace="etsy",
                marketplace_account_key=account_key,
                marketplace_account_label=account_label,
                txn_kind="payment_match",
                txn_id=row.row_id,
                date=row.date,
                description=description,
                external_ref=external_ref,
                clearing_amount=amount,
                splits=(
                    PlannedSplit(
                        account_guid=account_mapping.clearing_guid,
                        amount=amount,
                        memo=f"Etsy payment {external_ref}",
                        mapping_key="etsy:payment-match",
                    ),
                    PlannedSplit(
                        account_guid=None,
                        amount=-amount,
                        memo=f"Etsy payment offset {external_ref}",
                        mapping_key="etsy:payment-match-offset",
                    ),
                ),
                source_row_ids=(row.row_id,),
                warnings=(),
            )
        )

    candidates.sort(key=lambda txn: (txn.date, txn.txn_id))
    return tuple(candidates)


def build_ebay_transactions(
    data: EbayInputData,
    mapping: MappingConfig,
    *,
    account_key: str,
    account_label: str,
) -> tuple[tuple[PlannedTransaction, ...], tuple[str, ...], tuple[str, ...]]:
    transactions: list[PlannedTransaction] = []
    warnings: list[str] = []
    account_mapping = marketplace_mapping(
        mapping,
        account_key=account_key,
        marketplace="ebay",
        account_label=account_label,
    )

    rows = data.report_rows
    by_order: dict[str, list[EbayReportRow]] = defaultdict(list)

    for row in rows:
        if row.row_type == "Order" and row.order_number:
            by_order[row.order_number].append(row)

    consumed_rows: set[str] = set()

    for order_number, order_rows in by_order.items():
        sale_warnings: list[str] = []
        source_ids = [row.row_id for row in order_rows]

        clearing_amount = ZERO
        income_base = ZERO
        fee_totals: dict[str, Decimal] = defaultdict(lambda: ZERO)

        txn_date = min(row.date for row in order_rows)
        seller_tax_total = ZERO
        for row in order_rows:
            clearing_amount += row.net_amount
            income_base += row.item_subtotal + row.shipping_and_handling
            seller_tax_total += row.seller_collected_tax
            for col_name, fee_amount in row.fee_columns.items():
                fee_totals[col_name] += fee_amount

        splits: list[PlannedSplit] = []
        if account_mapping.clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.clearing_guid,
                    amount=clearing_amount,
                    memo=f"Order {order_number} net",
                )
            )
        else:
            sale_warnings.append("MISSING_ACCOUNT: eBay clearing account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=clearing_amount,
                    memo=f"Order {order_number} net",
                )
            )

        if account_mapping.income_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.income_guid,
                    amount=-income_base,
                    memo=f"Order {order_number} sales income",
                )
            )
        else:
            sale_warnings.append("MISSING_ACCOUNT: eBay income account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=-income_base,
                    memo=f"Order {order_number} sales income",
                )
            )

        for fee_column, raw_fee_value in sorted(fee_totals.items()):
            if raw_fee_value == ZERO:
                continue
            key = ebay_mapping_key(fee_column)
            account_guid = account_mapping.fee_accounts.get(key)
            if account_guid is None:
                sale_warnings.append(f"UNMAPPED: No eBay mapping for {key}")
            splits.append(
                PlannedSplit(
                    account_guid=account_guid,
                    amount=-raw_fee_value,
                    memo=fee_column,
                    mapping_key=key,
                )
            )

        if seller_tax_total > ZERO:
            sale_warnings.append(
                f"INFO: Seller collected tax present for eBay order {order_number} ({seller_tax_total}); no tax split created"
            )

        transactions.append(
            _finalize_transaction(
                dedupe_key=f"ebay:sale:{account_key}:{order_number}",
                marketplace="ebay",
                marketplace_account_key=account_key,
                marketplace_account_label=account_label,
                txn_kind="sale",
                txn_id=order_number,
                txn_date=txn_date,
                description=f"eBay Sale Order {order_number}",
                external_ref=order_number,
                clearing_amount=clearing_amount,
                splits=splits,
                source_row_ids=source_ids,
                warnings=sale_warnings,
            )
        )
        consumed_rows.update(source_ids)

    for row in rows:
        if row.row_type != "Refund":
            continue

        refund_warnings: list[str] = []
        order_number = row.order_number or row.row_id
        fee_total = ZERO
        splits: list[PlannedSplit] = []

        if account_mapping.clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.clearing_guid,
                    amount=row.net_amount,
                    memo=f"Refund {order_number} net",
                )
            )
        else:
            refund_warnings.append("MISSING_ACCOUNT: eBay clearing account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=row.net_amount,
                    memo=f"Refund {order_number} net",
                )
            )

        for fee_column, raw_fee_value in sorted(row.fee_columns.items()):
            if raw_fee_value == ZERO:
                continue
            key = ebay_mapping_key(fee_column)
            account_guid = account_mapping.fee_accounts.get(key)
            if account_guid is None:
                refund_warnings.append(f"UNMAPPED: No eBay mapping for {key}")

            fee_amount = -raw_fee_value
            fee_total += fee_amount
            splits.append(
                PlannedSplit(
                    account_guid=account_guid,
                    amount=fee_amount,
                    memo=f"Refund fee adj {fee_column}",
                    mapping_key=key,
                )
            )

        refunds_amount = -(row.net_amount + fee_total)
        if account_mapping.refunds_guid:
            splits.append(
                PlannedSplit(
                    account_guid=account_mapping.refunds_guid,
                    amount=refunds_amount,
                    memo=f"Refund expense {order_number}",
                    mapping_key="ebay:refunds",
                )
            )
        else:
            refund_warnings.append("MISSING_ACCOUNT: eBay refunds account is not selected")
            splits.append(
                PlannedSplit(
                    account_guid=None,
                    amount=refunds_amount,
                    memo=f"Refund expense {order_number}",
                    mapping_key="ebay:refunds",
                )
            )

        transactions.append(
            _finalize_transaction(
                dedupe_key=f"ebay:refund:{account_key}:{row.row_id}",
                marketplace="ebay",
                marketplace_account_key=account_key,
                marketplace_account_label=account_label,
                txn_kind="refund",
                txn_id=order_number,
                txn_date=row.date,
                description=f"eBay Refund {order_number}",
                external_ref=row.row_id,
                clearing_amount=row.net_amount,
                splits=splits,
                source_row_ids=[row.row_id],
                warnings=refund_warnings,
            )
        )
        consumed_rows.add(row.row_id)

    for row in rows:
        if row.row_id in consumed_rows:
            continue
        if row.row_type in {"Payout"}:
            continue
        if row.net_amount == ZERO:
            continue
        warnings.append(
            f"UNMATCHED_ROW: eBay row {row.row_number} ({row.row_type}) with net {row.net_amount} was not planned"
        )

    transactions.sort(key=lambda txn: (txn.date, txn.marketplace, txn.txn_kind, txn.txn_id))
    fee_columns = tuple(sorted(data.fee_columns))
    return tuple(transactions), tuple(warnings), fee_columns


def build_ebay_payout_match_candidates(
    data: EbayInputData,
    mapping: MappingConfig,
    *,
    account_key: str,
    account_label: str,
) -> tuple[PlannedTransaction, ...]:
    account_mapping = marketplace_mapping(
        mapping,
        account_key=account_key,
        marketplace="ebay",
        account_label=account_label,
    )
    if not account_mapping.clearing_guid:
        return ()

    candidates: list[PlannedTransaction] = []
    for row in data.report_rows:
        if row.row_type != "Payout":
            continue
        amount = abs(row.net_amount)
        if amount == ZERO:
            continue
        external_ref = row.raw.get("Payout ID") or row.raw.get("Reference ID") or row.row_id
        description = row.description or f"eBay Payout {external_ref}"
        candidates.append(
            PlannedTransaction(
                dedupe_key=f"ebay:payout:{account_key}:{row.row_id}",
                marketplace="ebay",
                marketplace_account_key=account_key,
                marketplace_account_label=account_label,
                txn_kind="payout_match",
                txn_id=external_ref,
                date=row.date,
                description=description,
                external_ref=external_ref,
                clearing_amount=amount,
                splits=(
                    PlannedSplit(
                        account_guid=account_mapping.clearing_guid,
                        amount=amount,
                        memo=f"eBay payout {external_ref}",
                        mapping_key="ebay:payout-match",
                    ),
                    PlannedSplit(
                        account_guid=None,
                        amount=-amount,
                        memo=f"eBay payout offset {external_ref}",
                        mapping_key="ebay:payout-match-offset",
                    ),
                ),
                source_row_ids=(row.row_id,),
                warnings=(),
            )
        )

    candidates.sort(key=lambda txn: (txn.date, txn.txn_id))
    return tuple(candidates)


def build_bank_transactions(
    bank_imports: tuple[tuple[BankImportSpec, tuple[BankStatementData, ...]], ...],
    mapping: MappingConfig,
    marketplace_transactions: tuple[PlannedTransaction, ...],
    marketplace_payout_candidates: tuple[PlannedTransaction, ...] = (),
) -> tuple[
    tuple[PlannedTransaction, ...],
    tuple[str, ...],
    tuple[BankMatchResult, ...],
    tuple[BankCategoryResult, ...],
]:
    transactions: list[PlannedTransaction] = []
    warnings: list[str] = []
    match_results: list[BankMatchResult] = []
    category_results: list[BankCategoryResult] = []
    used_marketplace_keys: set[str] = set()

    eligible_marketplace_transactions = [
        txn
        for txn in marketplace_transactions
        if not _has_blocking_warnings(txn)
        and _find_clearing_account_guid(txn) is not None
    ]

    for bank_import, statements in bank_imports:
        selected_account_guid = bank_import.account_guid
        import_key = selected_account_guid or "unselected-account"

        for statement in statements:
            if not statement.rows:
                warnings.append(
                    f"INFO: Bank/Card statement {statement.source_path} contained no rows in the selected date range"
                )
                continue

            for row in statement.rows:
                txn_warnings: list[str] = []
                txn_id = row.fitid or row.row_id
                bank_label = statement.account_id or statement.account_name or Path(statement.source_path).name
                description = row.description or row.memo or f"Bank/Card entry {txn_id}"
                source_ids = [row.row_id]
                dedupe_key = f"bank:{import_key}:{txn_id}"
                merchant_key = bank_merchant_key(description)

                if selected_account_guid is None:
                    txn_warnings.append(
                        "MISSING_ACCOUNT: No destination bank/card account selected for this import bundle"
                    )

                if row.amount == ZERO:
                    txn_warnings.append(f"INFO: Zero-amount bank/card row {txn_id}")

                match_result = _find_marketplace_match(
                    bank_date=row.date,
                    bank_amount=row.amount,
                    eligible_marketplace_transactions=eligible_marketplace_transactions,
                    marketplace_payout_candidates=list(marketplace_payout_candidates),
                    used_marketplace_keys=used_marketplace_keys,
                    bank_description=description,
                    bank_dedupe_key=dedupe_key,
                    bank_txn_id=txn_id,
                    override_ids=mapping.bank_match_overrides.get(dedupe_key, ()),
                )
                match_results.append(match_result)

                splits = [
                    PlannedSplit(
                        account_guid=selected_account_guid,
                        amount=row.amount,
                        memo=description,
                        mapping_key="bank:account",
                    )
                ]

                if match_result.status == "matched":
                    for target in match_result.targets:
                        splits.append(
                            PlannedSplit(
                                account_guid=target.account_guid,
                                amount=target.amount,
                                memo=target.memo,
                                mapping_key="bank:matched-clearing",
                            )
                        )
                    used_marketplace_keys.update(match_result.matched_transaction_ids)
                else:
                    if match_result.status == "ambiguous":
                        txn_warnings.append(
                            f"MATCH_AMBIGUOUS: Multiple marketplace match sets found for bank/card row {txn_id}"
                        )
                    if match_result.status == "invalid_override":
                        txn_warnings.append(
                            f"MATCH_OVERRIDE_INVALID: Manual bank/card match override is invalid for row {txn_id}"
                        )
                    txn_override_guid = mapping.bank_txn_account_overrides.get(dedupe_key)
                    merchant_account_guid = mapping.bank_merchant_accounts.get(merchant_key)
                    counterparty_guid = txn_override_guid or merchant_account_guid
                    if match_result.status == "unmatched" and counterparty_guid:
                        splits.append(
                            PlannedSplit(
                                account_guid=counterparty_guid,
                                amount=-row.amount,
                                memo=description,
                                mapping_key=(
                                    "bank:txn-override"
                                    if txn_override_guid
                                    else f"bank:merchant:{merchant_key}"
                                ),
                            )
                        )
                        category_results.append(
                            BankCategoryResult(
                                bank_dedupe_key=dedupe_key,
                                bank_txn_id=txn_id,
                                merchant_key=merchant_key,
                                description=description,
                                txn_date=row.date,
                                amount=row.amount,
                                mapped_account_guid=counterparty_guid,
                                mapping_source="transaction" if txn_override_guid else "merchant",
                            )
                        )
                    else:
                        if match_result.status == "unmatched":
                            txn_warnings.append(
                                f"UNMAPPED: No bank/card counterparty mapping for row {txn_id}"
                            )
                        splits.append(
                            PlannedSplit(
                                account_guid=None,
                                amount=-row.amount,
                                memo=description,
                                mapping_key="bank:unmapped",
                            )
                        )
                        if match_result.status == "unmatched":
                            category_results.append(
                                BankCategoryResult(
                                    bank_dedupe_key=dedupe_key,
                                    bank_txn_id=txn_id,
                                    merchant_key=merchant_key,
                                    description=description,
                                    txn_date=row.date,
                                    amount=row.amount,
                                    mapped_account_guid=None,
                                    mapping_source="unmapped",
                                )
                            )

                transactions.append(
                    _finalize_transaction(
                        dedupe_key=dedupe_key,
                        marketplace="bank",
                        marketplace_account_key=None,
                        marketplace_account_label=None,
                        txn_kind="statement",
                        txn_id=txn_id,
                        txn_date=row.date,
                        description=f"{bank_label}: {description}",
                        external_ref=row.fitid or row.row_id,
                        clearing_amount=row.amount,
                        splits=splits,
                        source_row_ids=source_ids,
                        warnings=txn_warnings,
                    )
                )

    transactions.sort(key=lambda txn: (txn.date, txn.marketplace, txn.txn_kind, txn.txn_id))
    category_results.sort(key=lambda result: (result.txn_date, result.description, result.bank_txn_id))
    return tuple(transactions), tuple(warnings), tuple(match_results), tuple(category_results)


def _find_marketplace_match(
    *,
    bank_date: date,
    bank_amount: Decimal,
    eligible_marketplace_transactions: list[PlannedTransaction],
    marketplace_payout_candidates: list[PlannedTransaction],
    used_marketplace_keys: set[str],
    bank_description: str,
    bank_dedupe_key: str,
    bank_txn_id: str,
    override_ids: tuple[str, ...],
) -> BankMatchResult:
    if override_ids:
        return _resolve_manual_override(
            override_ids=override_ids,
            bank_date=bank_date,
            bank_amount=bank_amount,
            eligible_marketplace_transactions=eligible_marketplace_transactions,
            bank_description=bank_description,
            bank_dedupe_key=bank_dedupe_key,
            bank_txn_id=bank_txn_id,
        )

    payout_candidates = [
        txn
        for txn in marketplace_payout_candidates
        if txn.dedupe_key not in used_marketplace_keys
        and txn.clearing_amount == bank_amount
        and abs((bank_date - txn.date).days) <= 7
    ]
    payout_candidates.sort(key=lambda txn: (abs((bank_date - txn.date).days), txn.date, txn.txn_id))
    if len(payout_candidates) == 1:
        matched_transaction = payout_candidates[0]
        clearing_guid = _find_clearing_account_guid(matched_transaction)
        match_source = matched_transaction.txn_kind.replace("_match", "")
        targets = (
            BankMatchTarget(
                account_guid=clearing_guid,
                amount=-matched_transaction.clearing_amount,
                memo=f"Matched clearing for {bank_description}",
                marketplace=matched_transaction.marketplace,
                marketplace_account_key=matched_transaction.marketplace_account_key,
                marketplace_account_label=matched_transaction.marketplace_account_label,
            ),
        ) if clearing_guid else ()
        return BankMatchResult(
            bank_dedupe_key=bank_dedupe_key,
            bank_txn_id=bank_txn_id,
            bank_description=bank_description,
            bank_date=bank_date,
            bank_amount=bank_amount,
            status="matched",
            match_source=match_source,
            matched_transaction_ids=(matched_transaction.dedupe_key,),
            targets=targets,
            marketplace_account_labels=tuple(
                value
                for value in [matched_transaction.marketplace_account_label]
                if value
            ),
        )
    if len(payout_candidates) > 1:
        match_source = payout_candidates[0].txn_kind.replace("_match", "")
        return BankMatchResult(
            bank_dedupe_key=bank_dedupe_key,
            bank_txn_id=bank_txn_id,
            bank_description=bank_description,
            bank_date=bank_date,
            bank_amount=bank_amount,
            status="ambiguous",
            match_source=match_source,
            matched_transaction_ids=tuple(txn.dedupe_key for txn in payout_candidates),
            targets=(),
            marketplace_account_labels=tuple(
                sorted(
                    {
                        txn.marketplace_account_label
                        for txn in payout_candidates
                        if txn.marketplace_account_label
                    }
                )
            ),
        )

    candidates = [
        txn
        for txn in eligible_marketplace_transactions
        if txn.dedupe_key not in used_marketplace_keys
        and txn.clearing_amount != ZERO
        and (
            bank_amount == ZERO
            or (txn.clearing_amount > ZERO and bank_amount > ZERO)
            or (txn.clearing_amount < ZERO and bank_amount < ZERO)
        )
    ]
    candidates = [
        txn
        for txn in candidates
        if abs((bank_date - txn.date).days) <= 7
    ]
    grouped_candidates: dict[str, list[PlannedTransaction]] = defaultdict(list)
    for txn in candidates:
        group_key = txn.marketplace_account_key or txn.marketplace
        grouped_candidates[group_key].append(txn)

    matches: list[tuple[PlannedTransaction, ...]] = []
    for group in grouped_candidates.values():
        group.sort(key=lambda txn: (abs((bank_date - txn.date).days), txn.date, txn.txn_id))
        matches.extend(_find_exact_transaction_subsets(group[:10], bank_amount))
    if len(matches) == 1:
        matched_transactions = tuple(sorted(matches[0], key=lambda txn: (txn.date, txn.txn_id, txn.dedupe_key)))
        grouped_targets: dict[tuple[str, str | None, str | None, str], Decimal] = defaultdict(lambda: ZERO)
        for txn in matched_transactions:
            clearing_guid = _find_clearing_account_guid(txn)
            if clearing_guid is None:
                continue
            grouped_targets[
                (
                    clearing_guid,
                    txn.marketplace_account_key,
                    txn.marketplace_account_label,
                    txn.marketplace,
                )
            ] += txn.clearing_amount
        targets = tuple(
            BankMatchTarget(
                account_guid=account_guid,
                amount=-amount,
                memo=f"Matched clearing for {bank_description}",
                marketplace=marketplace,
                marketplace_account_key=marketplace_account_key,
                marketplace_account_label=marketplace_account_label,
            )
            for (account_guid, marketplace_account_key, marketplace_account_label, marketplace), amount in sorted(grouped_targets.items())
        )
        return BankMatchResult(
            bank_dedupe_key=bank_dedupe_key,
            bank_txn_id=bank_txn_id,
            bank_description=bank_description,
            bank_date=bank_date,
            bank_amount=bank_amount,
            status="matched",
            match_source="auto",
            matched_transaction_ids=tuple(txn.dedupe_key for txn in matched_transactions),
            targets=targets,
            marketplace_account_labels=tuple(
                sorted(
                    {
                        txn.marketplace_account_label
                        for txn in matched_transactions
                        if txn.marketplace_account_label
                    }
                )
            ),
        )

    status = "ambiguous" if len(matches) > 1 else "unmatched"
    return BankMatchResult(
        bank_dedupe_key=bank_dedupe_key,
        bank_txn_id=bank_txn_id,
        bank_description=bank_description,
        bank_date=bank_date,
        bank_amount=bank_amount,
        status=status,
        match_source="auto",
        matched_transaction_ids=(),
        targets=(),
        marketplace_account_labels=(),
    )


def _resolve_manual_override(
    *,
    override_ids: tuple[str, ...],
    bank_date: date,
    bank_amount: Decimal,
    eligible_marketplace_transactions: list[PlannedTransaction],
    bank_description: str,
    bank_dedupe_key: str,
    bank_txn_id: str,
) -> BankMatchResult:
    by_id = {txn.dedupe_key: txn for txn in eligible_marketplace_transactions}
    matched_transactions: list[PlannedTransaction] = []
    for override_id in override_ids:
        txn = by_id.get(override_id)
        if txn is None:
            return BankMatchResult(
                bank_dedupe_key=bank_dedupe_key,
                bank_txn_id=bank_txn_id,
                bank_description=bank_description,
                bank_date=bank_date,
                bank_amount=bank_amount,
                status="invalid_override",
                match_source="manual",
                matched_transaction_ids=override_ids,
                targets=(),
            )
        matched_transactions.append(txn)

    total = ZERO
    grouped_targets: dict[tuple[str, str | None, str | None, str], Decimal] = defaultdict(lambda: ZERO)
    for txn in matched_transactions:
        total += txn.clearing_amount
        clearing_guid = _find_clearing_account_guid(txn)
        if clearing_guid is None:
            return BankMatchResult(
                bank_dedupe_key=bank_dedupe_key,
                bank_txn_id=bank_txn_id,
                bank_description=bank_description,
                bank_date=bank_date,
                bank_amount=bank_amount,
                status="invalid_override",
                match_source="manual",
                matched_transaction_ids=override_ids,
                targets=(),
            )
        grouped_targets[
            (
                clearing_guid,
                txn.marketplace_account_key,
                txn.marketplace_account_label,
                txn.marketplace,
            )
        ] += txn.clearing_amount

    if total != bank_amount:
        return BankMatchResult(
            bank_dedupe_key=bank_dedupe_key,
            bank_txn_id=bank_txn_id,
            bank_description=bank_description,
            bank_date=bank_date,
            bank_amount=bank_amount,
            status="invalid_override",
            match_source="manual",
            matched_transaction_ids=override_ids,
            targets=(),
        )

    targets = tuple(
        BankMatchTarget(
            account_guid=account_guid,
            amount=-amount,
            memo=f"Matched clearing for {bank_description}",
            marketplace=marketplace,
            marketplace_account_key=marketplace_account_key,
            marketplace_account_label=marketplace_account_label,
        )
        for (account_guid, marketplace_account_key, marketplace_account_label, marketplace), amount in sorted(grouped_targets.items())
    )
    return BankMatchResult(
        bank_dedupe_key=bank_dedupe_key,
        bank_txn_id=bank_txn_id,
        bank_description=bank_description,
        bank_date=bank_date,
        bank_amount=bank_amount,
        status="matched",
        match_source="manual",
        matched_transaction_ids=override_ids,
        targets=targets,
        marketplace_account_labels=tuple(
            sorted(
                {
                    txn.marketplace_account_label
                    for txn in matched_transactions
                    if txn.marketplace_account_label
                }
            )
        ),
    )


def _find_clearing_account_guid(transaction: PlannedTransaction) -> str | None:
    for split in transaction.splits:
        if split.amount == transaction.clearing_amount and split.account_guid:
            return split.account_guid
    return None


def _find_exact_transaction_subsets(
    candidates: list[PlannedTransaction],
    target_amount: Decimal,
) -> list[tuple[PlannedTransaction, ...]]:
    if not candidates:
        return []

    matches: list[tuple[PlannedTransaction, ...]] = []

    def dfs(start_index: int, current: list[PlannedTransaction], running_total: Decimal) -> None:
        if running_total == target_amount and current:
            matches.append(tuple(current))
            return
        if len(current) >= 6 or len(matches) > 1:
            return

        for index in range(start_index, len(candidates)):
            candidate = candidates[index]
            next_total = running_total + candidate.clearing_amount
            if target_amount >= ZERO and next_total > target_amount:
                continue
            if target_amount < ZERO and next_total < target_amount:
                continue
            current.append(candidate)
            dfs(index + 1, current, next_total)
            current.pop()
            if len(matches) > 1:
                return

    dfs(0, [], ZERO)
    return matches
