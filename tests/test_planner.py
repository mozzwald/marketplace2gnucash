from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from market2gnucash.core.dedupe_store import DedupeStore
from market2gnucash.core.models import MappingConfig, MarketplaceAccountMapping
from market2gnucash.core.planner import build_plan


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "sample_imports"
RS_SAMPLES = SAMPLES / "RS"
AMM_SAMPLES = SAMPLES / "AMM"


class PlannerTests(unittest.TestCase):
    def test_build_plan_supports_multiple_marketplace_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dedupe_store = DedupeStore(Path(tmp_dir) / "dedupe.sqlite3")
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


if __name__ == "__main__":
    unittest.main()
