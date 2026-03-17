from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
