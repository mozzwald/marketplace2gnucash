from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from market2gnucash.core.config_store import ConfigStore


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

            store.save_inputs("book-a", {"etsy_statement_path": "/tmp/a.csv"})
            store.save_inputs("book-b", {"etsy_statement_path": "/tmp/b.csv"})

            store.clear_book_state("book-a")

            self.assertEqual(store.load_inputs("book-a"), {})
            self.assertEqual(store.load_inputs("book-b"), {"etsy_statement_path": "/tmp/b.csv"})

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


if __name__ == "__main__":
    unittest.main()
