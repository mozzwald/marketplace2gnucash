from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from market2gnucash.core.dedupe_store import DedupeStore


class DedupeStoreTests(unittest.TestCase):
    def test_mark_and_query_imported_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            store = DedupeStore(db_path)

            book_id = "book-guid-123"
            keys = ["etsy:sale:1", "etsy:sale:2"]

            self.assertFalse(store.is_imported(book_id, keys[0]))
            store.mark_imported(book_id, keys)
            self.assertTrue(store.is_imported(book_id, keys[0]))

            existing = store.existing_keys(book_id, [*keys, "missing"])
            self.assertEqual(existing, set(keys))

    def test_clear_all_removes_import_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            store = DedupeStore(db_path)

            store.mark_imported("book-guid-123", ["etsy:sale:1", "etsy:sale:2"])
            self.assertEqual(store.import_count(), 2)

            store.clear_all()

            self.assertEqual(store.import_count(), 0)
            self.assertFalse(store.is_imported("book-guid-123", "etsy:sale:1"))


if __name__ == "__main__":
    unittest.main()
