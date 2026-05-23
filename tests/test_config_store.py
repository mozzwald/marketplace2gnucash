from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market2gnucash.core.config_store import ConfigStore
from market2gnucash.core.models import MappingConfig, MarketplaceAccountMapping


class ConfigStoreTests(unittest.TestCase):
    def test_last_book_path_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            store = ConfigStore(path)

            self.assertIsNone(store.load_last_book_path())
            store.save_last_book_path("/tmp/example.gnucash")

            self.assertEqual(store.load_last_book_path(), "/tmp/example.gnucash")

    def test_clear_book_state_removes_only_one_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            store = ConfigStore(path)

            store.save_inputs("book-a", {"marketplace_imports": [{"account_key": "etsy:a"}]})
            store.save_inputs("book-b", {"marketplace_imports": [{"account_key": "etsy:b"}]})

            store.clear_book_state("book-a")

            self.assertEqual(store.load_inputs("book-a"), {})
            self.assertEqual(store.load_inputs("book-b"), {"marketplace_imports": [{"account_key": "etsy:b"}]})

    def test_clear_all_removes_all_books(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            store = ConfigStore(path)

            store.save_inputs("book-a", {"foo": "bar"})
            store.save_inputs("book-b", {"baz": "qux"})
            store.save_last_book_path("/tmp/example.gnucash")
            self.assertEqual(store.book_ids(), ("book-a", "book-b"))

            store.clear_all()

            self.assertEqual(store.book_ids(), ())
            self.assertIsNone(store.load_last_book_path())
            self.assertEqual(store.load_inputs("book-a"), {})
            self.assertEqual(store.load_inputs("book-b"), {})

    def test_load_inputs_migrates_legacy_etsy_paths_to_monthly_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            store = ConfigStore(path)
            store.save_inputs(
                "book-a",
                {
                    "marketplace_imports": [
                        {
                            "marketplace": "etsy",
                            "account_key": "etsy:shop-a",
                            "account_label": "Shop A",
                            "etsy_statement_path": "/exports/etsy_statement_2026_4.csv",
                            "etsy_sold_orders_path": "/exports/EtsySoldOrders2026-4.csv",
                        }
                    ]
                },
            )

            inputs = store.load_inputs("book-a")

        marketplace_import = inputs["marketplace_imports"][0]
        self.assertEqual(marketplace_import["account_key"], "etsy:shop-a")
        self.assertEqual(
            marketplace_import["etsy_monthly_exports"],
            [
                {
                    "statement_path": "/exports/etsy_statement_2026_4.csv",
                    "sold_orders_path": "/exports/EtsySoldOrders2026-4.csv",
                }
            ],
        )

    def test_account_scoped_marketplace_mapping_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            store = ConfigStore(path)

            mapping = MappingConfig(
                marketplace_accounts={
                    "etsy:shop-a": MarketplaceAccountMapping(
                        marketplace="etsy",
                        account_label="Etsy Shop A",
                        clearing_guid="guid-clearing",
                        income_guid="guid-income",
                        refunds_guid="guid-refunds",
                        fee_accounts={"etsy:Fee:Listing fee": "guid-fee"},
                    )
                }
            )
            store.save_mapping("book-a", mapping)

            loaded = store.load_mapping("book-a")
            self.assertIn("etsy:shop-a", loaded.marketplace_accounts)
            self.assertEqual(loaded.marketplace_accounts["etsy:shop-a"].account_label, "Etsy Shop A")
            self.assertEqual(loaded.marketplace_accounts["etsy:shop-a"].fee_accounts["etsy:Fee:Listing fee"], "guid-fee")

    def test_bank_transfer_overrides_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            store = ConfigStore(path)

            mapping = MappingConfig(
                bank_transfer_overrides={
                    "bank:checking:T1": "bank:visa:T2",
                    "bank:visa:T2": "bank:checking:T1",
                }
            )
            store.save_mapping("book-a", mapping)

            loaded = store.load_mapping("book-a")
            self.assertEqual(loaded.bank_transfer_overrides["bank:checking:T1"], "bank:visa:T2")
            self.assertEqual(loaded.bank_transfer_overrides["bank:visa:T2"], "bank:checking:T1")


if __name__ == "__main__":
    unittest.main()
