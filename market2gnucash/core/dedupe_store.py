from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from market2gnucash.core.models import TransferAnchor, TransferAnchorResolution
from market2gnucash.core.paths import dedupe_db_path


class DedupeStore:
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
                CREATE TABLE IF NOT EXISTS imports (
                    book_id TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    PRIMARY KEY (book_id, dedupe_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transfer_anchors (
                    book_id TEXT NOT NULL,
                    anchor_dedupe_key TEXT NOT NULL,
                    bank_txn_id TEXT NOT NULL,
                    txn_date TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    source_account_guid TEXT NOT NULL,
                    source_account_label TEXT NOT NULL,
                    destination_account_guid TEXT NOT NULL,
                    destination_account_label TEXT NOT NULL,
                    description TEXT NOT NULL,
                    external_ref TEXT NOT NULL,
                    anchor_source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolved_counterpart_key TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    PRIMARY KEY (book_id, anchor_dedupe_key)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transfer_anchors_pending
                ON transfer_anchors (book_id, status, txn_date)
                """
            )
            conn.commit()

    def import_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM imports").fetchone()
        return int(row[0]) if row else 0

    def clear_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM imports")
            conn.execute("DELETE FROM transfer_anchors")
            conn.commit()
        self._init_db()

    def transfer_anchor_count(self, book_id: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM transfer_anchors WHERE status = 'pending'"
        params: tuple[str, ...] = ()
        if book_id is not None:
            query += " AND book_id = ?"
            params = (book_id,)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0

    def existing_keys(self, book_id: str, dedupe_keys: list[str]) -> set[str]:
        if not dedupe_keys:
            return set()
        placeholders = ",".join("?" for _ in dedupe_keys)
        query = (
            f"SELECT dedupe_key FROM imports WHERE book_id = ? "
            f"AND dedupe_key IN ({placeholders})"
        )
        with self._connect() as conn:
            rows = conn.execute(query, [book_id, *dedupe_keys]).fetchall()
        return {row[0] for row in rows}

    def is_imported(self, book_id: str, dedupe_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM imports WHERE book_id = ? AND dedupe_key = ?",
                (book_id, dedupe_key),
            ).fetchone()
        return row is not None

    def mark_imported(self, book_id: str, dedupe_keys: list[str]) -> None:
        if not dedupe_keys:
            return
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO imports (book_id, dedupe_key, imported_at) VALUES (?, ?, ?)",
                [(book_id, key, timestamp) for key in dedupe_keys],
            )
            conn.commit()

    def add_pending_transfer_anchors(self, book_id: str, anchors: list[TransferAnchor] | tuple[TransferAnchor, ...]) -> None:
        if not anchors:
            return
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO transfer_anchors (
                    book_id, anchor_dedupe_key, bank_txn_id, txn_date, amount,
                    source_account_guid, source_account_label,
                    destination_account_guid, destination_account_label,
                    description, external_ref, anchor_source, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(book_id, anchor_dedupe_key) DO UPDATE SET
                    bank_txn_id=excluded.bank_txn_id,
                    txn_date=excluded.txn_date,
                    amount=excluded.amount,
                    source_account_guid=excluded.source_account_guid,
                    source_account_label=excluded.source_account_label,
                    destination_account_guid=excluded.destination_account_guid,
                    destination_account_label=excluded.destination_account_label,
                    description=excluded.description,
                    external_ref=excluded.external_ref,
                    anchor_source=excluded.anchor_source,
                    status='pending',
                    resolved_counterpart_key=NULL,
                    resolved_at=NULL
                """,
                [
                    (
                        book_id,
                        anchor.anchor_dedupe_key,
                        anchor.bank_txn_id,
                        anchor.txn_date.isoformat(),
                        str(anchor.amount),
                        anchor.source_account_guid,
                        anchor.source_account_label,
                        anchor.destination_account_guid,
                        anchor.destination_account_label,
                        anchor.description,
                        anchor.external_ref,
                        anchor.anchor_source,
                        timestamp,
                    )
                    for anchor in anchors
                ],
            )
            conn.commit()

    def pending_transfer_anchors(self, book_id: str) -> tuple[TransferAnchor, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT anchor_dedupe_key, bank_txn_id, txn_date, amount,
                       source_account_guid, source_account_label,
                       destination_account_guid, destination_account_label,
                       description, external_ref, anchor_source
                FROM transfer_anchors
                WHERE book_id = ? AND status = 'pending'
                ORDER BY txn_date, anchor_dedupe_key
                """,
                (book_id,),
            ).fetchall()
        return tuple(
            TransferAnchor(
                anchor_dedupe_key=row[0],
                bank_txn_id=row[1],
                txn_date=datetime.fromisoformat(row[2]).date(),
                amount=Decimal(row[3]),
                source_account_guid=row[4],
                source_account_label=row[5],
                destination_account_guid=row[6],
                destination_account_label=row[7],
                description=row[8],
                external_ref=row[9],
                anchor_source=row[10],
            )
            for row in rows
        )

    def resolve_transfer_anchors(
        self,
        book_id: str,
        resolutions: list[TransferAnchorResolution] | tuple[TransferAnchorResolution, ...],
    ) -> None:
        if not resolutions:
            return
        resolved_at = datetime.now(UTC).isoformat()
        counterpart_keys = [resolution.counterpart_dedupe_key for resolution in resolutions]
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE transfer_anchors
                SET status = 'resolved',
                    resolved_counterpart_key = ?,
                    resolved_at = ?
                WHERE book_id = ? AND anchor_dedupe_key = ?
                """,
                [
                    (
                        resolution.counterpart_dedupe_key,
                        resolved_at,
                        book_id,
                        resolution.anchor_dedupe_key,
                    )
                    for resolution in resolutions
                ],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO imports (book_id, dedupe_key, imported_at) VALUES (?, ?, ?)",
                [(book_id, key, resolved_at) for key in counterpart_keys],
            )
            conn.commit()

    def clear_transfer_anchors(self, book_id: str | None = None) -> None:
        with self._connect() as conn:
            if book_id is None:
                conn.execute("DELETE FROM transfer_anchors")
            else:
                conn.execute("DELETE FROM transfer_anchors WHERE book_id = ?", (book_id,))
            conn.commit()
