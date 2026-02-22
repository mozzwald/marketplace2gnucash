from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from market2gnucash.core.parsers import parse_ebay_report, parse_etsy_inputs, parse_etsy_statement


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "sample_imports"


class ParserTests(unittest.TestCase):
    def test_parse_etsy_statement_and_sold_orders(self) -> None:
        etsy_data = parse_etsy_inputs(
            SAMPLES / "etsy_statement_2026_2.csv",
            SAMPLES / "EtsySoldOrders2026-2.csv",
        )

        self.assertGreater(len(etsy_data.statement_rows), 0)
        self.assertGreater(len(etsy_data.sold_orders), 0)

        sale_rows = [row for row in etsy_data.statement_rows if row.row_type == "Sale"]
        self.assertTrue(all(row.order_id for row in sale_rows))
        self.assertTrue(all(isinstance(row.net, Decimal) for row in sale_rows if row.net is not None))

        sold_order_ids = {row.order_id for row in etsy_data.sold_orders}
        self.assertIn("3977833995", sold_order_ids)

    def test_parse_ebay_report_header_and_fee_columns(self) -> None:
        ebay_data = parse_ebay_report(SAMPLES / "ebay_Transaction_report_20260201_20260221.csv")

        self.assertGreater(len(ebay_data.report_rows), 0)
        self.assertIn("Final Value Fee - fixed", ebay_data.fee_columns)
        self.assertIn("Regulatory operating fee", ebay_data.fee_columns)
        self.assertNotIn("Seller collected tax", ebay_data.fee_columns)

        order_rows = [row for row in ebay_data.report_rows if row.row_type == "Order"]
        self.assertGreater(len(order_rows), 0)
        self.assertTrue(all(isinstance(row.net_amount, Decimal) for row in order_rows))

    def test_parse_etsy_statement_extracts_listing_ids(self) -> None:
        rows = parse_etsy_statement(SAMPLES / "etsy_statement_2026_2.csv")
        listing_rows = [row for row in rows if row.title == "Listing fee"]
        self.assertGreater(len(listing_rows), 0)
        self.assertTrue(all(row.listing_id for row in listing_rows))


if __name__ == "__main__":
    unittest.main()
