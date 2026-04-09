from __future__ import annotations

from datetime import date
from decimal import Decimal
import tempfile
from pathlib import Path
import unittest

from market2gnucash.core.carryover_store import CarryoverStore
from market2gnucash.core.dedupe_store import DedupeStore
from market2gnucash.core.models import MappingConfig, MarketplaceAccountMapping, TransferAnchor
from market2gnucash.core.planner import build_plan
from market2gnucash.core.rules import bank_merchant_key


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "sample_imports"
RS_SAMPLES = SAMPLES / "Etsy-RS"
AMM_SAMPLES = SAMPLES / "Etsy-AMM"


class PlannerTests(unittest.TestCase):
    def test_build_plan_supports_multiple_marketplace_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dedupe_store = DedupeStore(Path(tmp_dir) / "dedupe.sqlite3")
            carryover_store = CarryoverStore(Path(tmp_dir) / "dedupe.sqlite3")
            mapping = MappingConfig(
                marketplace_accounts={
                    "etsy:shop-a": MarketplaceAccountMapping(
                        marketplace="etsy",
                        account_label="Shop A",
                        clearing_guid="guid-asset-etsy-a",
                        income_guid="guid-income-etsy-a",
                        refunds_guid="guid-refunds-etsy-a",
                        fee_accounts={
                            "etsy:Fee:Listing fee": "guid-fees-a",
                            "etsy:Fee:Transaction fee: Shipping": "guid-fees-a",
                            "etsy:Fee:Transaction fee:*": "guid-fees-a",
                            "etsy:Fee:Processing fee": "guid-fees-a",
                            "etsy:Fee:Credit for transaction fee on shipping": "guid-fees-a",
                            "etsy:Fee:Credit for processing fee": "guid-fees-a",
                        },
                    ),
                    "etsy:shop-b": MarketplaceAccountMapping(
                        marketplace="etsy",
                        account_label="Shop B",
                        clearing_guid="guid-asset-etsy-b",
                        income_guid="guid-income-etsy-b",
                        refunds_guid="guid-refunds-etsy-b",
                        fee_accounts={
                            "etsy:Fee:Listing fee": "guid-fees-b",
                            "etsy:Fee:Transaction fee: Shipping": "guid-fees-b",
                            "etsy:Fee:Transaction fee:*": "guid-fees-b",
                            "etsy:Fee:Processing fee": "guid-fees-b",
                            "etsy:Fee:Credit for transaction fee on shipping": "guid-fees-b",
                            "etsy:Fee:Credit for processing fee": "guid-fees-b",
                        },
                    ),
                }
            )

            plan = build_plan(
                book_id="book-1",
                dedupe_store=dedupe_store,
                carryover_store=carryover_store,
                mapping=mapping,
                marketplace_imports=[
                    {
                        "import_id": "import-a",
                        "marketplace": "etsy",
                        "account_key": "etsy:shop-a",
                        "account_label": "Shop A",
                        "etsy_statement_path": str(RS_SAMPLES / "etsy_statement_2026_1.csv"),
                        "etsy_sold_orders_path": str(RS_SAMPLES / "EtsySoldOrders2026-1.csv"),
                    },
                    {
                        "import_id": "import-b",
                        "marketplace": "etsy",
                        "account_key": "etsy:shop-b",
                        "account_label": "Shop B",
                        "etsy_statement_path": str(AMM_SAMPLES / "etsy_statement_2026_1.csv"),
                        "etsy_sold_orders_path": str(AMM_SAMPLES / "EtsySoldOrders2026-1.csv"),
                    },
                ],
                bank_imports=[],
                start_date=None,
                end_date=None,
            )

        marketplace_txns = [row.transaction for row in plan.transactions if row.transaction.marketplace == "etsy"]
        self.assertTrue(marketplace_txns)
        self.assertEqual(
            {txn.marketplace_account_label for txn in marketplace_txns},
            {"Shop A", "Shop B"},
        )
        self.assertEqual(
            len({txn.dedupe_key for txn in marketplace_txns}),
            len(marketplace_txns),
        )
        self.assertIn("etsy:shop-a", plan.marketplace_mapping_keys)
        self.assertIn("etsy:shop-b", plan.marketplace_mapping_keys)

    def test_build_plan_marks_transfer_counterpart_as_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dedupe_store = DedupeStore(Path(tmp_dir) / "dedupe.sqlite3")
            carryover_store = CarryoverStore(Path(tmp_dir) / "dedupe.sqlite3")
            checking_path = Path(tmp_dir) / "checking.csv"
            card_path = Path(tmp_dir) / "card.csv"
            checking_path.write_text(
                "\n".join(
                    [
                        "Date,Description,Amount,Reference",
                        "03/05/2026,VISA PAYMENT,-250.00,T1",
                    ]
                ),
                encoding="utf-8",
            )
            card_path.write_text(
                "\n".join(
                    [
                        "Date,Description,Amount,Reference",
                        "03/05/2026,PAYMENT FROM CHECKING,250.00,T2",
                    ]
                ),
                encoding="utf-8",
            )

            plan = build_plan(
                book_id="book-1",
                dedupe_store=dedupe_store,
                carryover_store=carryover_store,
                mapping=MappingConfig(),
                marketplace_imports=[],
                bank_imports=[
                    {"account_guid": "guid-checking", "statement_paths": [str(checking_path)], "csv_profiles": {}},
                    {"account_guid": "guid-card", "statement_paths": [str(card_path)], "csv_profiles": {}},
                ],
                start_date=None,
                end_date=None,
            )

        self.assertEqual(len(plan.bank_transfer_results), 2)
        statuses = {row.transaction.txn_id: row.status for row in plan.transactions if row.transaction.marketplace == "bank"}
        self.assertEqual(sorted(statuses.values()), ["deferred", "ready"])

    def test_build_plan_matches_bank_row_against_marketplace_carryover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            dedupe_store = DedupeStore(db_path)
            carryover_store = CarryoverStore(db_path)
            statement_path = Path(tmp_dir) / "etsy_statement.csv"
            sold_orders_path = Path(tmp_dir) / "etsy_sold.csv"
            bank_path = Path(tmp_dir) / "checking.csv"

            statement_path.write_text(
                "\n".join(
                    [
                        "Date,Type,Title,Info,Currency,Amount,Fees & Taxes,Net,Tax Details",
                        '01/30/2026,Deposit,"Deposit from Etsy","Payout","USD",38.18,,38.18,',
                    ]
                ),
                encoding="utf-8",
            )
            sold_orders_path.write_text(
                "Sale Date,Order ID,Currency,Order Value,Shipping,Sales Tax,Order Total\n",
                encoding="utf-8",
            )
            bank_path.write_text(
                "\n".join(
                    [
                        "Date,Description,Amount,Reference",
                        "02/02/2026,ETSY INC DEP,38.18,BANK1",
                    ]
                ),
                encoding="utf-8",
            )

            mapping = MappingConfig(
                marketplace_accounts={
                    "etsy:shop-a": MarketplaceAccountMapping(
                        marketplace="etsy",
                        account_label="Shop A",
                        clearing_guid="guid-asset-etsy",
                        income_guid="guid-income-etsy",
                        refunds_guid="guid-refunds-etsy",
                    )
                }
            )

            january_plan = build_plan(
                book_id="book-1",
                dedupe_store=dedupe_store,
                carryover_store=carryover_store,
                mapping=mapping,
                marketplace_imports=[
                    {
                        "import_id": "import-a",
                        "marketplace": "etsy",
                        "account_key": "etsy:shop-a",
                        "account_label": "Shop A",
                        "etsy_statement_path": str(statement_path),
                        "etsy_sold_orders_path": str(sold_orders_path),
                    }
                ],
                bank_imports=[],
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
            )

            self.assertEqual(january_plan.pending_carryover_count, 1)
            self.assertEqual(carryover_store.pending_count("book-1"), 1)

            february_plan = build_plan(
                book_id="book-1",
                dedupe_store=dedupe_store,
                carryover_store=carryover_store,
                mapping=mapping,
                marketplace_imports=[],
                bank_imports=[
                    {"account_guid": "guid-checking", "statement_paths": [str(bank_path)], "csv_profiles": {}}
                ],
                start_date=date(2026, 2, 1),
                end_date=date(2026, 2, 28),
            )

            self.assertEqual(len(february_plan.bank_match_results), 1)
            self.assertEqual(february_plan.bank_match_results[0].status, "matched")
            self.assertTrue(february_plan.matched_carryover_candidate_keys)
            bank_statuses = [row.status for row in february_plan.transactions if row.transaction.marketplace == "bank"]
            self.assertEqual(bank_statuses, ["ready"])

            carryover_store.resolve_candidates("book-1", list(february_plan.matched_carryover_candidate_keys))
            self.assertEqual(carryover_store.pending_count("book-1"), 0)

    def test_build_plan_exposes_balance_sheet_transfer_anchor_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            dedupe_store = DedupeStore(db_path)
            carryover_store = CarryoverStore(db_path)
            checking_path = Path(tmp_dir) / "checking.csv"
            checking_path.write_text(
                "\n".join(
                    [
                        "Date,Description,Amount,Reference",
                        "03/30/2026,VISA PAYMENT,-250.00,T1",
                    ]
                ),
                encoding="utf-8",
            )

            plan = build_plan(
                book_id="book-1",
                dedupe_store=dedupe_store,
                carryover_store=carryover_store,
                mapping=MappingConfig(
                    bank_txn_account_overrides={"bank:guid-checking:T1": "guid-card"}
                ),
                marketplace_imports=[],
                bank_imports=[
                    {"account_guid": "guid-checking", "statement_paths": [str(checking_path)], "csv_profiles": {}},
                ],
                start_date=None,
                end_date=None,
            )

        self.assertEqual(len(plan.transfer_anchor_candidates), 1)
        self.assertEqual(plan.transfer_anchor_candidates[0].anchor_dedupe_key, "bank:guid-checking:T1")
        self.assertEqual(plan.transfer_anchor_candidates[0].anchor_source, "transaction")

    def test_build_plan_exposes_balance_sheet_transfer_anchor_candidate_from_merchant_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            dedupe_store = DedupeStore(db_path)
            carryover_store = CarryoverStore(db_path)
            checking_path = Path(tmp_dir) / "checking.csv"
            checking_path.write_text(
                "\n".join(
                    [
                        "Date,Description,Amount,Reference",
                        "03/30/2026,VISA PAYMENT,-250.00,T1",
                    ]
                ),
                encoding="utf-8",
            )

            plan = build_plan(
                book_id="book-1",
                dedupe_store=dedupe_store,
                carryover_store=carryover_store,
                mapping=MappingConfig(
                    bank_merchant_accounts={bank_merchant_key("VISA PAYMENT"): "guid-card"}
                ),
                marketplace_imports=[],
                bank_imports=[
                    {"account_guid": "guid-checking", "statement_paths": [str(checking_path)], "csv_profiles": {}},
                ],
                start_date=None,
                end_date=None,
            )

        self.assertEqual(len(plan.transfer_anchor_candidates), 1)
        self.assertEqual(plan.transfer_anchor_candidates[0].anchor_source, "merchant_default")

    def test_build_plan_marks_imported_transfer_anchor_match_as_counterpart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            dedupe_store = DedupeStore(db_path)
            carryover_store = CarryoverStore(db_path)
            dedupe_store.add_pending_transfer_anchors(
                "book-1",
                [
                    TransferAnchor(
                        anchor_dedupe_key="bank:guid-checking:T1",
                        bank_txn_id="T1",
                        txn_date=date(2026, 3, 30),
                        amount=Decimal("-250.00"),
                        source_account_guid="guid-checking",
                        source_account_label="Assets:Checking",
                        destination_account_guid="guid-card",
                        destination_account_label="Liabilities:Visa",
                        description="VISA PAYMENT",
                        external_ref="T1",
                        anchor_source="transaction",
                    )
                ],
            )
            card_path = Path(tmp_dir) / "card.csv"
            card_path.write_text(
                "\n".join(
                    [
                        "Date,Description,Amount,Reference",
                        "04/02/2026,PAYMENT RECEIVED,250.00,T2",
                    ]
                ),
                encoding="utf-8",
            )

            plan = build_plan(
                book_id="book-1",
                dedupe_store=dedupe_store,
                carryover_store=carryover_store,
                mapping=MappingConfig(),
                marketplace_imports=[],
                bank_imports=[
                    {"account_guid": "guid-card", "statement_paths": [str(card_path)], "csv_profiles": {}},
                ],
                start_date=None,
                end_date=None,
            )

        statuses = [row.status for row in plan.transactions if row.transaction.marketplace == "bank"]
        self.assertEqual(statuses, ["counterpart"])
        self.assertEqual(len(plan.matched_transfer_anchor_resolutions), 1)
        self.assertEqual(
            plan.matched_transfer_anchor_resolutions[0].anchor_dedupe_key,
            "bank:guid-checking:T1",
        )


if __name__ == "__main__":
    unittest.main()
