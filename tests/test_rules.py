from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
import unittest

from market2gnucash.core.models import MappingConfig
from market2gnucash.core.parsers import parse_ebay_report, parse_etsy_inputs
from market2gnucash.core.rules import build_ebay_transactions, build_etsy_transactions


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "sample_imports"


class RuleEngineTests(unittest.TestCase):
    def _etsy_mapping(self) -> MappingConfig:
        etsy_fee_accounts = {
            "etsy:Fee:Listing fee": "guid-exp-listing",
            "etsy:Fee:Transaction fee: Shipping": "guid-exp-fee",
            "etsy:Fee:Transaction fee:*": "guid-exp-fee",
            "etsy:Fee:Processing fee": "guid-exp-fee",
            "etsy:Fee:Credit for transaction fee on shipping": "guid-exp-fee",
            "etsy:Fee:Credit for processing fee": "guid-exp-fee",
        }
        return MappingConfig(
            etsy_clearing_guid="guid-asset-etsy",
            etsy_income_guid="guid-income-etsy",
            etsy_refunds_guid="guid-exp-refunds",
            etsy_fee_accounts=etsy_fee_accounts,
        )

    def _ebay_mapping(self, fee_columns: tuple[str, ...]) -> MappingConfig:
        ebay_fee_accounts = {f"ebay:fee_col:{col}": "guid-exp-ebay-fees" for col in fee_columns}
        return MappingConfig(
            ebay_clearing_guid="guid-asset-ebay",
            ebay_income_guid="guid-income-ebay",
            ebay_refunds_guid="guid-exp-refunds-ebay",
            ebay_fee_accounts=ebay_fee_accounts,
        )

    def test_etsy_rules_one_sale_per_order_and_listing_per_row(self) -> None:
        etsy_data = parse_etsy_inputs(
            SAMPLES / "etsy_statement_2026_1.csv",
            SAMPLES / "EtsySoldOrders2026-1.csv",
        )

        transactions, warnings, _keys = build_etsy_transactions(etsy_data, self._etsy_mapping())

        sales = [txn for txn in transactions if txn.txn_kind == "sale"]
        refunds = [txn for txn in transactions if txn.txn_kind == "refund"]
        listings = [txn for txn in transactions if txn.txn_kind == "listing_fee"]

        self.assertEqual(len({txn.txn_id for txn in sales}), len(sales))

        listing_rows = [
            row for row in etsy_data.statement_rows if row.row_type == "Fee" and row.title == "Listing fee"
        ]
        self.assertEqual(len(listings), len(listing_rows))

        refund_rows = [row for row in etsy_data.statement_rows if row.row_type == "Refund"]
        self.assertEqual(len(refunds), len(refund_rows))

        for txn in transactions:
            self.assertEqual(sum(split.amount for split in txn.splits), Decimal("0"))

        self.assertTrue(all("UNBALANCED" not in warning for warning in warnings))

    def test_ebay_rules_one_sale_per_order(self) -> None:
        ebay_data = parse_ebay_report(SAMPLES / "eBay-Transaction_report_20260101_20260131.csv")
        mapping = self._ebay_mapping(ebay_data.fee_columns)

        transactions, _warnings, _columns = build_ebay_transactions(ebay_data, mapping)

        sales = [txn for txn in transactions if txn.txn_kind == "sale"]
        refunds = [txn for txn in transactions if txn.txn_kind == "refund"]

        self.assertEqual(len({txn.txn_id for txn in sales}), len(sales))
        self.assertGreaterEqual(len(refunds), 1)

        row_counts = Counter(row.order_number for row in ebay_data.report_rows if row.row_type == "Order")
        for txn in sales:
            self.assertIn(txn.txn_id, row_counts)

        for txn in transactions:
            self.assertEqual(sum(split.amount for split in txn.splits), Decimal("0"))

        for refund in refunds:
            income_memos = [split.memo for split in refund.splits if "sales income" in split.memo.lower()]
            self.assertEqual(income_memos, [])


if __name__ == "__main__":
    unittest.main()
