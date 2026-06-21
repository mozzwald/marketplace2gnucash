from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from market2gnucash.core.dedupe_store import DedupeStore, planned_transaction_fingerprint
from market2gnucash.core.models import (
    PlannedSplit,
    PlannedTransaction,
    TransferAnchor,
    TransferAnchorResolution,
)


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

    def test_mark_imported_stores_financial_fingerprint(self) -> None:
        transaction = PlannedTransaction(
            dedupe_key="ebay:sale:main:order-1",
            marketplace="ebay",
            marketplace_account_key="main",
            marketplace_account_label="Main",
            txn_kind="sale",
            txn_id="order-1",
            date=date(2026, 6, 13),
            description="Sale",
            external_ref="order-1",
            clearing_amount=Decimal("29.66"),
            splits=(
                PlannedSplit("clearing", Decimal("29.66"), "net"),
                PlannedSplit("income", Decimal("-29.66"), "income"),
            ),
            source_row_ids=("row-1",),
        )
        fingerprint = planned_transaction_fingerprint(transaction)

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = DedupeStore(Path(tmp_dir) / "dedupe.sqlite3")
            store.mark_imported(
                "book-guid-123",
                [transaction.dedupe_key],
                {transaction.dedupe_key: fingerprint},
            )

            records = store.imported_fingerprints(
                "book-guid-123",
                [transaction.dedupe_key],
            )

        self.assertEqual(records, {transaction.dedupe_key: fingerprint})

    def test_clear_all_removes_import_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            store = DedupeStore(db_path)

            store.mark_imported("book-guid-123", ["etsy:sale:1", "etsy:sale:2"])
            self.assertEqual(store.import_count(), 2)

            store.clear_all()

            self.assertEqual(store.import_count(), 0)
            self.assertFalse(store.is_imported("book-guid-123", "etsy:sale:1"))

    def test_pending_transfer_anchor_round_trip_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            store = DedupeStore(db_path)
            anchor = TransferAnchor(
                anchor_dedupe_key="bank:guid-checking:T1",
                bank_txn_id="T1",
                txn_date=date(2026, 3, 30),
                amount=Decimal("-250.00"),
                source_account_guid="guid-checking",
                source_account_label="Assets:Checking",
                destination_account_guid="guid-card",
                destination_account_label="Liabilities:Visa",
                description="Card Payment",
                external_ref="T1",
                anchor_source="transaction",
            )

            store.add_pending_transfer_anchors("book-guid-123", [anchor])
            self.assertEqual(store.transfer_anchor_count("book-guid-123"), 1)
            pending = store.pending_transfer_anchors("book-guid-123")
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].destination_account_guid, "guid-card")

            store.resolve_transfer_anchors(
                "book-guid-123",
                [
                    TransferAnchorResolution(
                        anchor_dedupe_key="bank:guid-checking:T1",
                        counterpart_dedupe_key="bank:guid-card:T2",
                    )
                ],
            )
            self.assertEqual(store.transfer_anchor_count("book-guid-123"), 0)
            self.assertTrue(store.is_imported("book-guid-123", "bank:guid-card:T2"))


if __name__ == "__main__":
    unittest.main()
