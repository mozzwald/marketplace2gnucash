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
from market2gnucash.core.dedupe_store import planned_transaction_fingerprint
from market2gnucash.core.gnucash_writer import GnuCashWriter

_BALANCE_SHEET_ACCOUNT_TYPES = {"ASSET", "BANK", "CASH", "CREDIT", "LIABILITY", "EQUITY"}


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
            plan_has_book_writes = any(
                status_row.status == "ready" for status_row in plan.transactions
            )
            if plan_has_book_writes:
                QMessageBox.critical(
                    self,
                    "Book Locked",
                    "Import blocked because the book is locked/open:\n" + "\n".join(lock_files),
                )
                return

        ready_transactions = [
            status_row.transaction for status_row in plan.transactions if status_row.status == "ready"
        ]
        pending_counterparts = plan.matched_transfer_anchor_resolutions
        pending_carryover = plan.matched_carryover_candidate_keys
        if not ready_transactions and not pending_counterparts and not pending_carryover:
            QMessageBox.warning(self, "Nothing to import", "No importable or finalizable transactions found.")
            return

        self.import_button.setEnabled(False)
        self.log_output.clear()
        self.progress_bar.setValue(0)

        try:
            written_keys: tuple[str, ...] = ()
            if ready_transactions:
                backup_path = create_timestamped_backup(book_path)
                self._log(f"Created backup: {backup_path}")

                writer = GnuCashWriter(Path(book_path))

                def on_progress(current: int, total: int, description: str) -> None:
                    percent = int((current / total) * 100)
                    self.progress_bar.setValue(percent)
                    self.status_label.setText(f"Importing {current}/{total}: {description}")
                    QApplication.processEvents()

                result = writer.write_transactions(ready_transactions, progress_cb=on_progress)
                written_keys = result.written_keys
                transactions_by_key = {
                    transaction.dedupe_key: transaction for transaction in ready_transactions
                }
                fingerprints = {
                    key: planned_transaction_fingerprint(transactions_by_key[key])
                    for key in written_keys
                    if key in transactions_by_key
                }
                self.app_state["dedupe_store"].mark_imported(
                    book_id,
                    list(written_keys),
                    fingerprints,
                )

            ready_keys = set(written_keys)
            anchors_to_add = self._pending_transfer_anchors_for_import(plan, ready_keys)
            if anchors_to_add:
                self.app_state["dedupe_store"].add_pending_transfer_anchors(book_id, list(anchors_to_add))
            if pending_counterparts:
                self.app_state["dedupe_store"].resolve_transfer_anchors(book_id, list(pending_counterparts))
            self.app_state["carryover_store"].resolve_candidates(
                book_id,
                list(pending_carryover),
            )
            self.app_state["plan_result"] = None

            if written_keys:
                self._log(f"Imported transactions: {len(written_keys)}")
            if anchors_to_add:
                self._log(f"Saved pending transfer anchors: {len(anchors_to_add)}")
            if pending_counterparts:
                self._log(f"Finalized transfer counterparts: {len(pending_counterparts)}")
            if pending_carryover:
                self._log(
                    f"Resolved carryover candidates: {len(pending_carryover)}"
                )
            self.progress_bar.setValue(100 if ready_transactions else 0)
            self.status_label.setText("Import complete. Re-run Preview to refresh statuses.")
            QMessageBox.information(
                self,
                "Import complete",
                (
                    f"Imported {len(written_keys)} transactions."
                    if ready_transactions
                    else f"Finalized {len(pending_counterparts)} counterpart match(es)."
                ),
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

    def _pending_transfer_anchors_for_import(self, plan, ready_keys: set[str]):
        if not ready_keys:
            return ()
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})
        anchors = []
        for anchor in plan.transfer_anchor_candidates:
            if anchor.anchor_dedupe_key not in ready_keys:
                continue
            destination = accounts_by_guid.get(anchor.destination_account_guid)
            if destination is None or destination.account_type not in _BALANCE_SHEET_ACCOUNT_TYPES:
                continue
            source = accounts_by_guid.get(anchor.source_account_guid)
            if source is None:
                continue
            anchors.append(
                type(anchor)(
                    anchor_dedupe_key=anchor.anchor_dedupe_key,
                    bank_txn_id=anchor.bank_txn_id,
                    txn_date=anchor.txn_date,
                    amount=anchor.amount,
                    source_account_guid=anchor.source_account_guid,
                    source_account_label=source.full_name,
                    destination_account_guid=anchor.destination_account_guid,
                    destination_account_label=destination.full_name,
                    description=anchor.description,
                    external_ref=anchor.external_ref,
                    anchor_source=anchor.anchor_source,
                )
            )
        return tuple(anchors)
