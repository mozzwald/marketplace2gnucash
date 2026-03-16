from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
            conn.commit()

    def import_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM imports").fetchone()
        return int(row[0]) if row else 0

    def clear_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM imports")
            conn.commit()
        self._init_db()

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
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO imports (book_id, dedupe_key, imported_at) VALUES (?, ?, ?)",
                [(book_id, key, timestamp) for key in dedupe_keys],
            )
            conn.commit()
