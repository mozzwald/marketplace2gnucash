from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.book_io import create_timestamped_backup, detect_book_locks
from market2gnucash.core.gnucash_writer import GnuCashWriter


class ImportTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        self.import_button = QPushButton("Import Into Book")
        self.import_button.clicked.connect(self.perform_import)

        self.status_label = QLabel("Ready.")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)

        layout.addWidget(self.import_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_output)

    def refresh_from_state(self) -> None:
        plan = self.app_state.get("plan_result")
        if not plan:
            self.status_label.setText("Generate a preview before importing.")
            return

        ready = sum(1 for txn in plan.transactions if txn.status == "ready")
        self.status_label.setText(f"Ready transactions: {ready}")

    def perform_import(self) -> None:
        plan = self.app_state.get("plan_result")
        book_path = self.app_state.get("book_path")
        book_id = self.app_state.get("book_id")

        if not plan:
            QMessageBox.warning(self, "No Preview", "Generate a preview in the Preview tab first.")
            return
        if not book_path or not book_id:
            QMessageBox.warning(self, "No Book", "Open a book first.")
            return

        lock_files = detect_book_locks(book_path)
        if lock_files:
            QMessageBox.critical(
                self,
                "Book Locked",
                "Import blocked because the book is locked/open:\n" + "\n".join(lock_files),
            )
            return

        ready_transactions = [
            status_row.transaction for status_row in plan.transactions if status_row.status == "ready"
        ]
        if not ready_transactions:
            QMessageBox.warning(self, "Nothing to import", "No ready transactions found.")
            return

        self.import_button.setEnabled(False)
        self.log_output.clear()
        self.progress_bar.setValue(0)

        try:
            backup_path = create_timestamped_backup(book_path)
            self._log(f"Created backup: {backup_path}")

            writer = GnuCashWriter(Path(book_path))

            def on_progress(current: int, total: int, description: str) -> None:
                percent = int((current / total) * 100)
                self.progress_bar.setValue(percent)
                self.status_label.setText(f"Importing {current}/{total}: {description}")
                QApplication.processEvents()

            result = writer.write_transactions(ready_transactions, progress_cb=on_progress)
            self.app_state["dedupe_store"].mark_imported(book_id, list(result.written_keys))
            self.app_state["plan_result"] = None

            self._log(f"Imported transactions: {len(result.written_keys)}")
            self.progress_bar.setValue(100)
            self.status_label.setText("Import complete. Re-run Preview to refresh statuses.")
            QMessageBox.information(
                self,
                "Import complete",
                f"Imported {len(result.written_keys)} transactions. Backup: {backup_path.name}",
            )
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            self.status_label.setText("Import failed.")
            QMessageBox.critical(self, "Import failed", str(exc))
        finally:
            self.import_button.setEnabled(True)

        # Refresh preview statuses now that dedupe has changed
        self.app_state["notify_state_changed"]()

    def _log(self, line: str) -> None:
        self.log_output.appendPlainText(line)
