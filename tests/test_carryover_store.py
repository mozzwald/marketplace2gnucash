from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
