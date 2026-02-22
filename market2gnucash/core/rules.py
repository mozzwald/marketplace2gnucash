from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from market2gnucash.core.decimal_utils import ZERO
from market2gnucash.core.models import (
    EbayInputData,
    EbayReportRow,
    EtsyInputData,
    EtsyStatementRow,
    MappingConfig,
    PlannedSplit,
    PlannedTransaction,
)


def etsy_mapping_key(row: EtsyStatementRow) -> str:
    return f"etsy:{row.row_type}:{row.title}"


def etsy_mapping_key_candidates(row: EtsyStatementRow) -> tuple[str, ...]:
    exact = etsy_mapping_key(row)
    if row.row_type == "Fee" and row.title.startswith("Transaction fee:") and row.title != "Transaction fee: Shipping":
        return (exact, "etsy:Fee:Transaction fee:*")
    return (exact,)


def ebay_mapping_key(column_name: str) -> str:
    return f"ebay:fee_col:{column_name}"


def _split_sum(splits: list[PlannedSplit]) -> Decimal:
    total = ZERO
    for split in splits:
        total += split.amount
    return total


def _finalize_transaction(
    *,
    dedupe_key: str,
    marketplace: str,
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


def _lookup_etsy_fee_account(mapping: MappingConfig, row: EtsyStatementRow) -> tuple[str | None, str | None]:
    for key in etsy_mapping_key_candidates(row):
        account_guid = mapping.etsy_fee_accounts.get(key)
        if account_guid:
            return key, account_guid
    return None, None


def build_etsy_transactions(
    data: EtsyInputData,
    mapping: MappingConfig,
) -> tuple[tuple[PlannedTransaction, ...], tuple[str, ...], tuple[str, ...]]:
    transactions: list[PlannedTransaction] = []
    warnings: list[str] = []

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
        if mapping.etsy_clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.etsy_clearing_guid,
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

        if mapping.etsy_income_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.etsy_income_guid,
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
            key, account_guid = _lookup_etsy_fee_account(mapping, row)
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
                dedupe_key=f"etsy:sale:{order_id}",
                marketplace="etsy",
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
            account_guid = mapping.etsy_fee_accounts.get(mapping_key)
            if account_guid is None:
                listing_warnings.append(f"UNMAPPED: No Etsy mapping for {mapping_key}")

            clearing_amount = row.net or ZERO
            splits: list[PlannedSplit] = []
            if mapping.etsy_clearing_guid:
                splits.append(
                    PlannedSplit(
                        account_guid=mapping.etsy_clearing_guid,
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
                    dedupe_key=f"etsy:listing_fee:{row.row_id}",
                    marketplace="etsy",
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
        if mapping.etsy_clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.etsy_clearing_guid,
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
            key, account_guid = _lookup_etsy_fee_account(mapping, adj)
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
        if mapping.etsy_refunds_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.etsy_refunds_guid,
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
                dedupe_key=f"etsy:refund:{row.row_id}",
                marketplace="etsy",
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


def build_ebay_transactions(
    data: EbayInputData,
    mapping: MappingConfig,
) -> tuple[tuple[PlannedTransaction, ...], tuple[str, ...], tuple[str, ...]]:
    transactions: list[PlannedTransaction] = []
    warnings: list[str] = []

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
        if mapping.ebay_clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.ebay_clearing_guid,
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

        if mapping.ebay_income_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.ebay_income_guid,
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
            account_guid = mapping.ebay_fee_accounts.get(key)
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
                dedupe_key=f"ebay:sale:{order_number}",
                marketplace="ebay",
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

        if mapping.ebay_clearing_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.ebay_clearing_guid,
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
            account_guid = mapping.ebay_fee_accounts.get(key)
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
        if mapping.ebay_refunds_guid:
            splits.append(
                PlannedSplit(
                    account_guid=mapping.ebay_refunds_guid,
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
                dedupe_key=f"ebay:refund:{row.row_id}",
                marketplace="ebay",
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
