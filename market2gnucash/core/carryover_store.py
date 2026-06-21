from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from market2gnucash.core.models import CarryoverCandidate, PlannedSplit, PlannedTransaction
from market2gnucash.core.paths import dedupe_db_path


class CarryoverStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or dedupe_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS carryover_candidates (
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
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    PRIMARY KEY (book_id, candidate_key)
                )
                """
            )
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(carryover_candidates)").fetchall()
            }
            if "invalidated_at" not in columns:
                conn.execute("ALTER TABLE carryover_candidates ADD COLUMN invalidated_at TEXT")
            if "invalidation_reason" not in columns:
                conn.execute("ALTER TABLE carryover_candidates ADD COLUMN invalidation_reason TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_carryover_pending
                ON carryover_candidates (book_id, status, txn_date)
                """
            )
            conn.commit()

    def upsert_pending_candidates(self, book_id: str, candidates: tuple[CarryoverCandidate, ...] | list[CarryoverCandidate]) -> None:
        if not candidates:
            return
        created_at = datetime.now(UTC).isoformat()
        rows = [
            (
                book_id,
                candidate.candidate_key,
                candidate.candidate_type,
                candidate.source_scope,
                candidate.txn_date.isoformat(),
                str(candidate.amount),
                candidate.description,
                json.dumps(candidate.payload, sort_keys=True),
                "pending",
                created_at,
            )
            for candidate in candidates
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO carryover_candidates (
                    book_id, candidate_key, candidate_type, source_scope,
                    txn_date, amount, description, payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(book_id, candidate_key) DO UPDATE SET
                    candidate_type=excluded.candidate_type,
                    source_scope=excluded.source_scope,
                    txn_date=excluded.txn_date,
                    amount=excluded.amount,
                    description=excluded.description,
                    payload_json=excluded.payload_json,
                    status=CASE
                        WHEN carryover_candidates.status = 'invalidated' THEN 'invalidated'
                        ELSE 'pending'
                    END,
                    resolved_at=CASE
                        WHEN carryover_candidates.status = 'invalidated'
                        THEN carryover_candidates.resolved_at
                        ELSE NULL
                    END,
                    invalidated_at=carryover_candidates.invalidated_at,
                    invalidation_reason=carryover_candidates.invalidation_reason
                """,
                rows,
            )
            conn.commit()

    def list_pending_candidates(self, book_id: str) -> tuple[CarryoverCandidate, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate_key, candidate_type, source_scope, txn_date, amount, description, payload_json
                FROM carryover_candidates
                WHERE book_id = ? AND status = 'pending'
                ORDER BY txn_date, candidate_key
                """,
                (book_id,),
            ).fetchall()
        return tuple(
            self._row_to_candidate(
                candidate_key=row[0],
                candidate_type=row[1],
                source_scope=row[2],
                txn_date=row[3],
                amount=row[4],
                description=row[5],
                payload_json=row[6],
            )
            for row in rows
        )

    def list_invalidated_candidates(self, book_id: str) -> tuple[CarryoverCandidate, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate_key, candidate_type, source_scope, txn_date, amount,
                       description, payload_json, invalidated_at, invalidation_reason
                FROM carryover_candidates
                WHERE book_id = ? AND status = 'invalidated'
                ORDER BY invalidated_at DESC, txn_date, candidate_key
                """,
                (book_id,),
            ).fetchall()
        return tuple(
            self._row_to_candidate(
                candidate_key=row[0],
                candidate_type=row[1],
                source_scope=row[2],
                txn_date=row[3],
                amount=row[4],
                description=row[5],
                payload_json=row[6],
                invalidated_at=row[7],
                invalidation_reason=row[8],
            )
            for row in rows
        )

    def pending_count(self, book_id: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM carryover_candidates WHERE status = 'pending'"
        params: tuple[str, ...] = ()
        if book_id is not None:
            query += " AND book_id = ?"
            params = (book_id,)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0

    def resolve_candidates(self, book_id: str, candidate_keys: list[str] | tuple[str, ...]) -> None:
        if not candidate_keys:
            return
        resolved_at = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE carryover_candidates
                SET status = 'resolved', resolved_at = ?
                WHERE book_id = ? AND candidate_key = ?
                """,
                [(resolved_at, book_id, key) for key in candidate_keys],
            )
            conn.commit()

    def invalidate_candidates(
        self,
        book_id: str,
        candidate_keys: list[str] | tuple[str, ...],
        reason: str,
    ) -> None:
        if not candidate_keys:
            return
        invalidated_at = datetime.now(UTC).isoformat()
        normalized_reason = reason.strip()
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE carryover_candidates
                SET status = 'invalidated',
                    invalidated_at = ?,
                    invalidation_reason = ?,
                    resolved_at = NULL
                WHERE book_id = ? AND candidate_key = ? AND status = 'pending'
                """,
                [
                    (invalidated_at, normalized_reason, book_id, key)
                    for key in candidate_keys
                ],
            )
            conn.commit()

    def restore_candidates(
        self,
        book_id: str,
        candidate_keys: list[str] | tuple[str, ...],
    ) -> None:
        if not candidate_keys:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE carryover_candidates
                SET status = 'pending',
                    invalidated_at = NULL,
                    invalidation_reason = NULL,
                    resolved_at = NULL
                WHERE book_id = ? AND candidate_key = ? AND status = 'invalidated'
                """,
                [(book_id, key) for key in candidate_keys],
            )
            conn.commit()

    def clear_pending(self, book_id: str | None = None) -> None:
        with self._connect() as conn:
            if book_id is None:
                conn.execute("DELETE FROM carryover_candidates WHERE status = 'pending'")
            else:
                conn.execute(
                    "DELETE FROM carryover_candidates WHERE book_id = ? AND status = 'pending'",
                    (book_id,),
                )
            conn.commit()

    def clear_book(self, book_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM carryover_candidates WHERE book_id = ?", (book_id,))
            conn.commit()

    def clear_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM carryover_candidates")
            conn.commit()

    def _row_to_candidate(
        self,
        *,
        candidate_key: str,
        candidate_type: str,
        source_scope: str,
        txn_date: str,
        amount: str,
        description: str,
        payload_json: str,
        invalidated_at: str | None = None,
        invalidation_reason: str | None = None,
    ) -> CarryoverCandidate:
        payload = json.loads(payload_json)
        transaction = self._deserialize_transaction(payload["transaction"])
        return CarryoverCandidate(
            candidate_key=candidate_key,
            candidate_type=candidate_type,
            source_scope=source_scope,
            txn_date=date.fromisoformat(txn_date),
            amount=Decimal(amount),
            description=description,
            payload=payload,
            transaction=transaction,
            invalidated_at=invalidated_at,
            invalidation_reason=invalidation_reason,
        )

    @staticmethod
    def serialize_transaction(transaction: PlannedTransaction) -> dict[str, object]:
        return {
            "dedupe_key": transaction.dedupe_key,
            "marketplace": transaction.marketplace,
            "marketplace_account_key": transaction.marketplace_account_key,
            "marketplace_account_label": transaction.marketplace_account_label,
            "txn_kind": transaction.txn_kind,
            "txn_id": transaction.txn_id,
            "date": transaction.date.isoformat(),
            "description": transaction.description,
            "external_ref": transaction.external_ref,
            "clearing_amount": str(transaction.clearing_amount),
            "splits": [
                {
                    "account_guid": split.account_guid,
                    "amount": str(split.amount),
                    "memo": split.memo,
                    "mapping_key": split.mapping_key,
                }
                for split in transaction.splits
            ],
            "source_row_ids": list(transaction.source_row_ids),
            "warnings": list(transaction.warnings),
        }

    @staticmethod
    def _deserialize_transaction(payload: dict[str, object]) -> PlannedTransaction:
        splits = tuple(
            PlannedSplit(
                account_guid=split.get("account_guid") if isinstance(split.get("account_guid"), str) else None,
                amount=Decimal(str(split["amount"])),
                memo=str(split["memo"]),
                mapping_key=split.get("mapping_key") if isinstance(split.get("mapping_key"), str) else None,
            )
            for split in payload.get("splits", [])
            if isinstance(split, dict)
        )
        return PlannedTransaction(
            dedupe_key=str(payload["dedupe_key"]),
            marketplace=str(payload["marketplace"]),
            marketplace_account_key=payload.get("marketplace_account_key") if isinstance(payload.get("marketplace_account_key"), str) else None,
            marketplace_account_label=payload.get("marketplace_account_label") if isinstance(payload.get("marketplace_account_label"), str) else None,
            txn_kind=str(payload["txn_kind"]),
            txn_id=str(payload["txn_id"]),
            date=date.fromisoformat(str(payload["date"])),
            description=str(payload["description"]),
            external_ref=str(payload["external_ref"]),
            clearing_amount=Decimal(str(payload["clearing_amount"])),
            splits=splits,
            source_row_ids=tuple(str(value) for value in payload.get("source_row_ids", [])),
            warnings=tuple(str(value) for value in payload.get("warnings", [])),
        )
