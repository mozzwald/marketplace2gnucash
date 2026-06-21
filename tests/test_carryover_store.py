from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from market2gnucash.core.carryover_store import CarryoverStore
from market2gnucash.core.models import CarryoverCandidate, PlannedSplit, PlannedTransaction


class CarryoverStoreTests(unittest.TestCase):
    def test_upsert_list_and_resolve_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            store = CarryoverStore(db_path)
            transaction = PlannedTransaction(
                dedupe_key="etsy:deposit:carry:shop-a:row-1",
                marketplace="etsy",
                marketplace_account_key="shop-a",
                marketplace_account_label="Shop A",
                txn_kind="deposit_match",
                txn_id="row-1",
                date=date(2026, 1, 30),
                description="Etsy Deposit",
                external_ref="row-1",
                clearing_amount=Decimal("38.18"),
                splits=(
                    PlannedSplit(account_guid="guid-clearing", amount=Decimal("38.18"), memo="Deposit"),
                    PlannedSplit(account_guid=None, amount=Decimal("-38.18"), memo="Offset"),
                ),
                source_row_ids=("row-1",),
            )
            candidate = CarryoverCandidate(
                candidate_key=transaction.dedupe_key,
                candidate_type="deposit",
                source_scope="shop-a",
                txn_date=transaction.date,
                amount=transaction.clearing_amount,
                description=transaction.description,
                payload={"transaction": CarryoverStore.serialize_transaction(transaction)},
                transaction=transaction,
            )

            store.upsert_pending_candidates("book-1", [candidate])
            self.assertEqual(store.pending_count("book-1"), 1)
            loaded = store.list_pending_candidates("book-1")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].transaction.dedupe_key, candidate.candidate_key)
            self.assertEqual(loaded[0].amount, Decimal("38.18"))

            store.resolve_candidates("book-1", [candidate.candidate_key])
            self.assertEqual(store.pending_count("book-1"), 0)

    def test_init_migrates_existing_carryover_table_for_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE carryover_candidates (
                        book_id TEXT NOT NULL,
                        candidate_key TEXT NOT NULL,
                        candidate_type TEXT NOT NULL,
                        source_scope TEXT NOT NULL,
                        txn_date TEXT NOT NULL,
                        amount TEXT NOT NULL,
                        description TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        resolved_at TEXT,
                        PRIMARY KEY (book_id, candidate_key)
                    )
                    """
                )

            CarryoverStore(db_path)

            with sqlite3.connect(db_path) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(carryover_candidates)").fetchall()
                }
            self.assertIn("invalidated_at", columns)
            self.assertIn("invalidation_reason", columns)

    def test_invalidated_candidate_is_auditable_and_not_reactivated_by_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dedupe.sqlite3"
            store = CarryoverStore(db_path)
            transaction = PlannedTransaction(
                dedupe_key="etsy:deposit:carry:shop-a:row-1",
                marketplace="etsy",
                marketplace_account_key="shop-a",
                marketplace_account_label="Shop A",
                txn_kind="deposit_match",
                txn_id="row-1",
                date=date(2026, 6, 5),
                description="Etsy Deposit",
                external_ref="row-1",
                clearing_amount=Decimal("46.64"),
                splits=(
                    PlannedSplit(account_guid="guid-clearing", amount=Decimal("46.64"), memo="Deposit"),
                    PlannedSplit(account_guid=None, amount=Decimal("-46.64"), memo="Offset"),
                ),
                source_row_ids=("row-1",),
            )
            candidate = CarryoverCandidate(
                candidate_key=transaction.dedupe_key,
                candidate_type="deposit",
                source_scope="shop-a",
                txn_date=transaction.date,
                amount=transaction.clearing_amount,
                description=transaction.description,
                payload={"transaction": CarryoverStore.serialize_transaction(transaction)},
                transaction=transaction,
            )

            store.upsert_pending_candidates("book-1", [candidate])
            store.invalidate_candidates(
                "book-1",
                [candidate.candidate_key],
                "Export was assigned to the wrong marketplace account",
            )

            self.assertEqual(store.pending_count("book-1"), 0)
            invalidated = store.list_invalidated_candidates("book-1")
            self.assertEqual(len(invalidated), 1)
            self.assertIsNotNone(invalidated[0].invalidated_at)
            self.assertEqual(
                invalidated[0].invalidation_reason,
                "Export was assigned to the wrong marketplace account",
            )

            store.upsert_pending_candidates("book-1", [candidate])
            self.assertEqual(store.pending_count("book-1"), 0)
            self.assertEqual(len(store.list_invalidated_candidates("book-1")), 1)

            store.clear_pending("book-1")
            self.assertEqual(len(store.list_invalidated_candidates("book-1")), 1)

            store.restore_candidates("book-1", [candidate.candidate_key])
            self.assertEqual(store.pending_count("book-1"), 1)
            self.assertEqual(store.list_invalidated_candidates("book-1"), ())


if __name__ == "__main__":
    unittest.main()
