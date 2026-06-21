from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.models import CarryoverCandidate, MappingConfig
from market2gnucash.core.paths import app_data_dir, config_json_path, dedupe_db_path


class InvalidatedCarryoversDialog(QDialog):
    def __init__(
        self,
        *,
        candidates: tuple[CarryoverCandidate, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Invalidated Carryovers")
        self.resize(1000, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Invalidated carryovers are excluded from matching. Select records to restore them to pending."
            )
        )

        self.candidates_table = QTableWidget()
        self.candidates_table.setColumnCount(8)
        self.candidates_table.setHorizontalHeaderLabels(
            [
                "Invalidated",
                "Date",
                "Marketplace",
                "Market Acct",
                "Type",
                "Amount",
                "Description",
                "Reason",
            ]
        )
        self.candidates_table.setRowCount(len(candidates))
        self.candidates_table.verticalHeader().setVisible(False)
        self.candidates_table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.candidates_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidates_table.setSortingEnabled(False)

        for row_index, candidate in enumerate(candidates):
            txn = candidate.transaction
            invalidated_at = candidate.invalidated_at or ""
            if "T" in invalidated_at:
                invalidated_at = invalidated_at.replace("T", " ").split("+")[0]
            row_items = [
                QTableWidgetItem(invalidated_at),
                QTableWidgetItem(candidate.txn_date.isoformat()),
                QTableWidgetItem(txn.marketplace),
                QTableWidgetItem(txn.marketplace_account_label or candidate.source_scope),
                QTableWidgetItem(candidate.candidate_type),
                QTableWidgetItem(str(candidate.amount)),
                QTableWidgetItem(candidate.description),
                QTableWidgetItem(candidate.invalidation_reason or ""),
            ]
            row_items[0].setData(Qt.ItemDataRole.UserRole, candidate.candidate_key)
            for col_index, item in enumerate(row_items):
                self.candidates_table.setItem(row_index, col_index, item)

        self.candidates_table.setSortingEnabled(True)
        self.candidates_table.resizeColumnsToContents()
        layout.addWidget(self.candidates_table)

        buttons_row = QHBoxLayout()
        restore_button = QPushButton("Restore Selected")
        restore_button.clicked.connect(self.accept)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons_row.addStretch()
        buttons_row.addWidget(restore_button)
        buttons_row.addWidget(close_button)
        layout.addLayout(buttons_row)

    def selected_candidate_keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        seen_rows: set[int] = set()
        for item in self.candidates_table.selectedItems():
            if item.row() in seen_rows:
                continue
            seen_rows.add(item.row())
            key_item = self.candidates_table.item(item.row(), 0)
            if key_item is None:
                continue
            value = key_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, str):
                keys.append(value)
        return tuple(keys)


class SettingsTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        paths_group = QGroupBox("Storage")
        paths_layout = QFormLayout(paths_group)
        self.data_dir_label = QLabel()
        self.config_path_label = QLabel()
        self.db_path_label = QLabel()
        self.book_count_label = QLabel()
        self.import_count_label = QLabel()
        self.carryover_count_label = QLabel()
        self.transfer_anchor_count_label = QLabel()
        paths_layout.addRow("Data directory", self.data_dir_label)
        paths_layout.addRow("Config file", self.config_path_label)
        paths_layout.addRow("Import history DB", self.db_path_label)
        paths_layout.addRow("Saved books", self.book_count_label)
        paths_layout.addRow("Imported rows", self.import_count_label)
        paths_layout.addRow("Pending carryover", self.carryover_count_label)
        paths_layout.addRow("Pending transfer anchors", self.transfer_anchor_count_label)
        layout.addWidget(paths_group)

        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)

        reset_current_row = QHBoxLayout()
        reset_current_button = QPushButton("Reset Current Book Settings")
        reset_current_button.clicked.connect(self._reset_current_book_settings)
        reset_current_row.addWidget(reset_current_button)
        reset_current_row.addWidget(
            QLabel("Clear saved inputs and mappings for the currently open book.")
        )
        reset_current_row.addStretch()
        actions_layout.addLayout(reset_current_row)

        clear_history_row = QHBoxLayout()
        clear_history_button = QPushButton("Clear Import History Database")
        clear_history_button.clicked.connect(self._clear_import_history)
        clear_history_row.addWidget(clear_history_button)
        clear_history_row.addWidget(
            QLabel("Remove all dedupe/import history records so prior imports can be planned again.")
        )
        clear_history_row.addStretch()
        actions_layout.addLayout(clear_history_row)

        clear_carryover_row = QHBoxLayout()
        clear_carryover_button = QPushButton("Clear Carryover Queue")
        clear_carryover_button.clicked.connect(self._clear_carryover_queue)
        clear_carryover_row.addWidget(clear_carryover_button)
        clear_carryover_row.addWidget(
            QLabel("Remove unresolved marketplace carryover candidates saved for future matching.")
        )
        clear_carryover_row.addStretch()
        actions_layout.addLayout(clear_carryover_row)

        invalidated_carryover_row = QHBoxLayout()
        invalidated_carryover_button = QPushButton("Manage Invalidated Carryovers")
        invalidated_carryover_button.clicked.connect(self._manage_invalidated_carryovers)
        invalidated_carryover_row.addWidget(invalidated_carryover_button)
        invalidated_carryover_row.addWidget(
            QLabel("View audit history and restore invalidated marketplace carryovers.")
        )
        invalidated_carryover_row.addStretch()
        actions_layout.addLayout(invalidated_carryover_row)

        reset_all_row = QHBoxLayout()
        reset_all_button = QPushButton("Reset All Saved App Settings")
        reset_all_button.clicked.connect(self._reset_all_settings)
        reset_all_row.addWidget(reset_all_button)
        reset_all_row.addWidget(
            QLabel("Clear saved mappings and inputs for every book in this app.")
        )
        reset_all_row.addStretch()
        actions_layout.addLayout(reset_all_row)

        refresh_row = QHBoxLayout()
        refresh_button = QPushButton("Refresh Summary")
        refresh_button.clicked.connect(self.refresh_from_state)
        refresh_row.addWidget(refresh_button)
        refresh_row.addStretch()
        actions_layout.addLayout(refresh_row)

        layout.addWidget(actions_group)

        notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes_group)
        notes_layout.addWidget(
            QLabel("These actions only affect market2gnucash app data. They do not modify any GnuCash book file.")
        )
        notes_layout.addWidget(
            QLabel("After clearing import history, rerun Preview to rebuild duplicate detection against an empty database.")
        )
        layout.addWidget(notes_group)

        layout.addStretch()

    def refresh_from_state(self) -> None:
        config_store = self.app_state["config_store"]
        dedupe_store = self.app_state["dedupe_store"]
        carryover_store = self.app_state["carryover_store"]
        self.data_dir_label.setText(str(app_data_dir()))
        self.config_path_label.setText(str(config_json_path()))
        self.db_path_label.setText(str(dedupe_db_path()))
        self.book_count_label.setText(str(len(config_store.book_ids())))
        self.import_count_label.setText(str(dedupe_store.import_count()))
        self.carryover_count_label.setText(str(carryover_store.pending_count()))
        self.transfer_anchor_count_label.setText(str(dedupe_store.transfer_anchor_count()))

    def _reset_runtime_state(self) -> None:
        self.app_state["mapping_config"] = MappingConfig()
        self.app_state["inputs"] = {}
        self.app_state["plan_result"] = None
        self.app_state["marketplace_mapping_keys"] = {}

    def _confirm(self, title: str, text: str) -> bool:
        result = QMessageBox.question(self, title, text)
        return result == QMessageBox.StandardButton.Yes

    def _clear_import_history(self) -> None:
        if not self._confirm(
            "Clear Import History",
            "Delete all dedupe/import history records from the app database?",
        ):
            return

        self.app_state["dedupe_store"].clear_all()
        self.app_state["plan_result"] = None
        self.app_state["notify_state_changed"]()

    def _clear_carryover_queue(self) -> None:
        if not self._confirm(
            "Clear Carryover Queue",
            "Delete all pending marketplace carryover candidates from the app database?",
        ):
            return

        self.app_state["carryover_store"].clear_pending()
        self.app_state["plan_result"] = None
        self.app_state["notify_state_changed"]()

    def _manage_invalidated_carryovers(self) -> None:
        book_id = self.app_state.get("book_id")
        if not book_id:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        candidates = self.app_state["carryover_store"].list_invalidated_candidates(book_id)
        if not candidates:
            QMessageBox.information(
                self,
                "Invalidated Carryovers",
                "The current book has no invalidated carryover records.",
            )
            return

        dialog = InvalidatedCarryoversDialog(candidates=candidates, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        candidate_keys = dialog.selected_candidate_keys()
        if not candidate_keys:
            return
        if not self._confirm(
            "Restore Carryovers",
            f"Restore {len(candidate_keys)} selected carryover record(s) to pending matching?",
        ):
            return

        self.app_state["carryover_store"].restore_candidates(book_id, candidate_keys)
        self.app_state["plan_result"] = None
        self.app_state["notify_state_changed"]()

    def _reset_current_book_settings(self) -> None:
        book_id = self.app_state.get("book_id")
        if not book_id:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return
        if not self._confirm(
            "Reset Current Book",
            "Clear saved inputs and mappings for the currently open book?",
        ):
            return

        self.app_state["config_store"].clear_book_state(book_id)
        self.app_state["carryover_store"].clear_book(book_id)
        self.app_state["dedupe_store"].clear_transfer_anchors(book_id)
        self._reset_runtime_state()
        self.app_state["notify_state_changed"]()

    def _reset_all_settings(self) -> None:
        if not self._confirm(
            "Reset All Settings",
            "Clear saved inputs and mappings for all books in the app config?",
        ):
            return

        self.app_state["config_store"].clear_all()
        self.app_state["carryover_store"].clear_all()
        self.app_state["dedupe_store"].clear_transfer_anchors()
        self._reset_runtime_state()
        self.app_state["notify_state_changed"]()
