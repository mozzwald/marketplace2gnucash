from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from market2gnucash.core.models import BankCsvProfile
from market2gnucash.core.parsers import (
    parse_bank_statement_file,
    parse_ebay_report,
    parse_etsy_inputs,
    parse_etsy_statement,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "sample_imports"
RS_SAMPLES = SAMPLES / "Etsy-RS"


class ParserTests(unittest.TestCase):
    def test_parse_etsy_statement_and_sold_orders(self) -> None:
        etsy_data = parse_etsy_inputs(
            RS_SAMPLES / "etsy_statement_2026_2.csv",
            RS_SAMPLES / "EtsySoldOrders2026-2.csv",
        )

        self.assertGreater(len(etsy_data.statement_rows), 0)
        self.assertGreater(len(etsy_data.sold_orders), 0)

        sale_rows = [row for row in etsy_data.statement_rows if row.row_type == "Sale"]
        self.assertTrue(all(row.order_id for row in sale_rows))
        self.assertTrue(all(isinstance(row.net, Decimal) for row in sale_rows if row.net is not None))

        sold_order_ids = {row.order_id for row in etsy_data.sold_orders}
        self.assertIn("3977833995", sold_order_ids)

    def test_parse_ebay_report_header_and_fee_columns(self) -> None:
        ebay_data = parse_ebay_report(SAMPLES / "eBay-RS" / "eBay-Transaction_report_20260101_20260221.csv")

        self.assertGreater(len(ebay_data.report_rows), 0)
        self.assertIn("Final Value Fee - fixed", ebay_data.fee_columns)
        self.assertIn("Regulatory operating fee", ebay_data.fee_columns)
        self.assertNotIn("Seller collected tax", ebay_data.fee_columns)

        order_rows = [row for row in ebay_data.report_rows if row.row_type == "Order"]
        self.assertGreater(len(order_rows), 0)
        self.assertTrue(all(isinstance(row.net_amount, Decimal) for row in order_rows))

    def test_parse_etsy_statement_extracts_listing_ids(self) -> None:
        rows = parse_etsy_statement(RS_SAMPLES / "etsy_statement_2026_2.csv")
        listing_rows = [row for row in rows if row.title == "Listing fee"]
        self.assertGreater(len(listing_rows), 0)
        self.assertTrue(all(row.listing_id for row in listing_rows))

    def test_parse_etsy_statement_row_ids_remain_stable_when_file_grows(self) -> None:
        initial_csv = "\n".join(
            [
                "Date,Type,Title,Info,Currency,Amount,Fees & Taxes,Net,Tax Details",
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
            ]
        )
        grown_csv = "\n".join(
            [
                "Date,Type,Title,Info,Currency,Amount,Fees & Taxes,Net,Tax Details",
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
                '04/13/2026,Fee,"Listing fee","Listing #123","USD",-0.20,,-0.20,',
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "etsy_statement_2026_4.csv"
            path.write_text(initial_csv, encoding="utf-8")
            initial_rows = parse_etsy_statement(path)

            path.write_text(grown_csv, encoding="utf-8")
            grown_rows = parse_etsy_statement(path)

        self.assertEqual([row.row_id for row in initial_rows], [row.row_id for row in grown_rows[:5]])
        self.assertEqual(len({row.row_id for row in grown_rows}), 7)

    def test_parse_bank_csv_with_debit_credit_columns(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Debit,Credit,Reference,Currency",
                "03/01/2026,Coffee Shop,4.50,,A1,USD",
                "03/02/2026,Payroll,,1500.00,A2,USD",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "statement.csv"
            path.write_text(csv_text, encoding="utf-8")

            statement = parse_bank_statement_file(path)

        self.assertEqual(statement.source_format, "csv")
        self.assertEqual(len(statement.rows), 2)
        self.assertEqual(statement.currency, "USD")
        self.assertEqual(statement.rows[0].amount, Decimal("-4.50"))
        self.assertEqual(statement.rows[1].amount, Decimal("1500.00"))
        self.assertEqual(statement.rows[0].fitid, "A1")

    def test_parse_bank_ofx_sgml_statement(self) -> None:
        ofx_text = "\n".join(
            [
                "OFXHEADER:100",
                "DATA:OFXSGML",
                "VERSION:102",
                "",
                "<OFX>",
                "<CURDEF>USD",
                "<BANKMSGSRSV1>",
                "<STMTTRNRS>",
                "<STMTRS>",
                "<BANKACCTFROM>",
                "<ACCTID>123456789",
                "</BANKACCTFROM>",
                "<BANKTRANLIST>",
                "<STMTTRN>",
                "<TRNTYPE>DEBIT",
                "<DTPOSTED>20260301120000[-6:CST]",
                "<TRNAMT>-12.34",
                "<FITID>fit-1",
                "<NAME>Bookstore",
                "<MEMO>Order 55",
                "</STMTTRN>",
                "<STMTTRN>",
                "<TRNTYPE>CREDIT",
                "<DTPOSTED>20260302120000[-6:CST]",
                "<TRNAMT>200.00",
                "<FITID>fit-2",
                "<NAME>Refund",
                "</STMTTRN>",
                "</BANKTRANLIST>",
                "</STMTRS>",
                "</STMTTRNRS>",
                "</BANKMSGSRSV1>",
                "</OFX>",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "statement.ofx"
            path.write_text(ofx_text, encoding="utf-8")

            statement = parse_bank_statement_file(path)

        self.assertEqual(statement.source_format, "ofx")
        self.assertEqual(statement.account_id, "123456789")
        self.assertEqual(statement.currency, "USD")
        self.assertEqual(len(statement.rows), 2)
        self.assertEqual(statement.rows[0].amount, Decimal("-12.34"))
        self.assertEqual(statement.rows[0].fitid, "fit-1")
        self.assertEqual(statement.rows[0].memo, "Order 55")

    def test_parse_sample_hometown_csv(self) -> None:
        statement = parse_bank_statement_file(SAMPLES / "Hometown" / "Hometown_20260101-20260327.csv")

        self.assertEqual(statement.source_format, "csv")
        self.assertEqual(statement.account_id, "336661")
        self.assertGreater(len(statement.rows), 40)
        self.assertEqual(statement.rows[0].amount, Decimal("-431.79"))
        self.assertIn("WELLS FARGO CARD CCPYMT", statement.rows[0].description)

    def test_parse_sample_credit_card_headerless_csv(self) -> None:
        statement = parse_bank_statement_file(SAMPLES / "Wells-Fargo" / "Wells-Fargo_20260101-20260327.csv")

        self.assertEqual(statement.source_format, "csv")
        self.assertGreater(len(statement.rows), 10)
        self.assertEqual(statement.rows[4].amount, Decimal("-100.00"))
        self.assertEqual(statement.rows[4].description, "PIRATE SHIP * POSTAGE PRT.SH WY")
        self.assertEqual(statement.rows[4].account_name, "Wells-Fargo_20260101-20260327")

    def test_parse_headerless_csv_with_explicit_profile(self) -> None:
        profile = BankCsvProfile(
            has_header=False,
            date_column="__col_0__",
            amount_column="__col_1__",
            memo_column="__col_3__",
            description_column="__col_4__",
        )

        statement = parse_bank_statement_file(SAMPLES / "Wells-Fargo" / "Wells-Fargo_20260101-20260327.csv", csv_profile=profile)

        self.assertGreater(len(statement.rows), 10)
        self.assertEqual(statement.rows[6].amount, Decimal("-20.00"))
        self.assertIn("OPENAI", statement.rows[6].description)

    def test_parse_bank_csv_row_ids_remain_stable_when_file_grows_without_reference_ids(self) -> None:
        initial_csv = "\n".join(
            [
                "Date,Description,Amount",
                "04/13/2026,VISA PURCHASE,-1.00",
                "04/13/2026,VISA PURCHASE,-1.00",
                "04/13/2026,VISA PURCHASE,-1.00",
            ]
        )
        grown_csv = "\n".join(
            [
                "Date,Description,Amount",
                "04/13/2026,VISA PURCHASE,-1.00",
                "04/13/2026,VISA PURCHASE,-1.00",
                "04/13/2026,VISA PURCHASE,-1.00",
                "04/13/2026,VISA PURCHASE,-1.00",
                "04/13/2026,VISA PURCHASE,-1.00",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "card.csv"
            path.write_text(initial_csv, encoding="utf-8")
            initial_statement = parse_bank_statement_file(path)

            path.write_text(grown_csv, encoding="utf-8")
            grown_statement = parse_bank_statement_file(path)

        self.assertEqual(
            [row.row_id for row in initial_statement.rows],
            [row.row_id for row in grown_statement.rows[:3]],
        )
        self.assertEqual(len({row.row_id for row in grown_statement.rows}), 5)


if __name__ == "__main__":
    unittest.main()
