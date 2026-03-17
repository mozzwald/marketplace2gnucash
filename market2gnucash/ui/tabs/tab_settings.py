from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.models import MappingConfig
from market2gnucash.core.paths import app_data_dir, config_json_path, dedupe_db_path


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
        paths_layout.addRow("Data directory", self.data_dir_label)
        paths_layout.addRow("Config file", self.config_path_label)
        paths_layout.addRow("Import history DB", self.db_path_label)
        paths_layout.addRow("Saved books", self.book_count_label)
        paths_layout.addRow("Imported rows", self.import_count_label)
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
        self.data_dir_label.setText(str(app_data_dir()))
        self.config_path_label.setText(str(config_json_path()))
        self.db_path_label.setText(str(dedupe_db_path()))
        self.book_count_label.setText(str(len(config_store.book_ids())))
        self.import_count_label.setText(str(dedupe_store.import_count()))

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
        self._reset_runtime_state()
        self.app_state["notify_state_changed"]()

    def _reset_all_settings(self) -> None:
        if not self._confirm(
            "Reset All Settings",
            "Clear saved inputs and mappings for all books in the app config?",
        ):
            return

        self.app_state["config_store"].clear_all()
        self._reset_runtime_state()
        self.app_state["notify_state_changed"]()
