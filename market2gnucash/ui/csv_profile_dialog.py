from __future__ import annotations

import csv
import io
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.models import BankCsvProfile
from market2gnucash.core.parsers import inspect_bank_csv_file, suggest_bank_csv_profile


def _read_csv_rows(path: str) -> tuple[str, list[list[str]]]:
    csv_path = Path(path)
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            sample = csv_path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Could not decode CSV file {csv_path}")

    try:
        dialect = csv.Sniffer().sniff(sample[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows = list(csv.reader(io.StringIO(sample), dialect=dialect))
    return getattr(dialect, "delimiter", ","), rows


def _column_label(column_name: str, sample_value: str | None = None) -> str:
    if column_name.startswith("__col_") and column_name.endswith("__"):
        try:
            index = int(column_name[len("__col_") : -len("__")])
        except ValueError:
            index = None
        if index is not None:
            label = f"Column {index + 1}"
            if sample_value:
                return f"{label}: {sample_value[:40]}"
            return label
    if sample_value:
        return f"{column_name} ({sample_value[:40]})"
    return column_name


class CsvProfileDialog(QDialog):
    def __init__(
        self,
        file_paths: list[str],
        existing_profiles: dict[str, BankCsvProfile | dict[str, object]] | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure CSV Columns")
        self.resize(960, 640)

        self._file_paths = file_paths
        self._profiles: dict[str, BankCsvProfile] = {}
        for path, profile in (existing_profiles or {}).items():
            if isinstance(profile, BankCsvProfile):
                self._profiles[path] = profile
            elif isinstance(profile, dict):
                self._profiles[path] = BankCsvProfile(
                    has_header=bool(profile.get("has_header", True)),
                    date_column=profile.get("date_column") if isinstance(profile.get("date_column"), str) else None,
                    amount_column=profile.get("amount_column") if isinstance(profile.get("amount_column"), str) else None,
                    debit_column=profile.get("debit_column") if isinstance(profile.get("debit_column"), str) else None,
                    credit_column=profile.get("credit_column") if isinstance(profile.get("credit_column"), str) else None,
                    description_column=profile.get("description_column") if isinstance(profile.get("description_column"), str) else None,
                    memo_column=profile.get("memo_column") if isinstance(profile.get("memo_column"), str) else None,
                    id_column=profile.get("id_column") if isinstance(profile.get("id_column"), str) else None,
                    check_number_column=profile.get("check_number_column") if isinstance(profile.get("check_number_column"), str) else None,
                    currency_column=profile.get("currency_column") if isinstance(profile.get("currency_column"), str) else None,
                    account_id_column=profile.get("account_id_column") if isinstance(profile.get("account_id_column"), str) else None,
                    account_name_column=profile.get("account_name_column") if isinstance(profile.get("account_name_column"), str) else None,
                )

        self._raw_rows: dict[str, list[list[str]]] = {}
        self._preview_columns: tuple[str, ...] = ()
        self._last_path: str | None = None

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("CSV file"))
        self.file_combo = QComboBox()
        for path in file_paths:
            self.file_combo.addItem(Path(path).name, path)
        self.file_combo.currentIndexChanged.connect(self._load_selected_file)
        top_row.addWidget(self.file_combo, 1)
        layout.addLayout(top_row)

        self.header_checkbox = QCheckBox("First row contains column headers")
        self.header_checkbox.toggled.connect(self._refresh_column_options)
        layout.addWidget(self.header_checkbox)

        form = QFormLayout()
        self.date_combo = self._make_column_combo()
        self.amount_combo = self._make_column_combo()
        self.debit_combo = self._make_column_combo()
        self.credit_combo = self._make_column_combo()
        self.description_combo = self._make_column_combo()
        self.memo_combo = self._make_column_combo()
        self.id_combo = self._make_column_combo()
        self.check_combo = self._make_column_combo()
        self.currency_combo = self._make_column_combo()
        self.account_id_combo = self._make_column_combo()
        self.account_name_combo = self._make_column_combo()

        form.addRow("Date", self.date_combo)
        form.addRow("Amount", self.amount_combo)
        form.addRow("Debit", self.debit_combo)
        form.addRow("Credit", self.credit_combo)
        form.addRow("Description", self.description_combo)
        form.addRow("Memo", self.memo_combo)
        form.addRow("Reference / FITID", self.id_combo)
        form.addRow("Check number", self.check_combo)
        form.addRow("Currency", self.currency_combo)
        form.addRow("Account ID", self.account_id_combo)
        form.addRow("Account name", self.account_name_combo)
        layout.addLayout(form)

        self.preview_table = QTableWidget()
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.preview_table.verticalHeader().setVisible(False)
        layout.addWidget(self.preview_table, 1)

        self.hint_label = QLabel(
            "Set either Amount or Debit/Credit. Date is required."
        )
        layout.addWidget(self.hint_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        restore = buttons.button(QDialogButtonBox.RestoreDefaults)
        if restore is not None:
            restore.setText("Auto Detect")
            restore.clicked.connect(self._reset_to_detected_profile)
        layout.addWidget(buttons)

        self._load_selected_file()

    def _make_column_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem("(none)", None)
        return combo

    def _selected_path(self) -> str:
        return str(self.file_combo.currentData(Qt.UserRole) or self.file_combo.currentData() or "")

    def _load_selected_file(self) -> None:
        self._save_current_profile()
        path = self._selected_path()
        if not path:
            return

        try:
            inspect_bank_csv_file(path)
            _delimiter, rows = _read_csv_rows(path)
        except Exception as exc:
            QMessageBox.warning(self, "CSV Error", str(exc))
            return

        self._raw_rows[path] = rows
        profile = self._profiles.get(path) or suggest_bank_csv_profile(path)
        self.header_checkbox.blockSignals(True)
        self.header_checkbox.setChecked(profile.has_header)
        self.header_checkbox.blockSignals(False)
        self._refresh_column_options()
        self._apply_profile_to_widgets(profile)
        self._last_path = path

    def _current_columns_and_rows(self) -> tuple[tuple[str, ...], list[list[str]]]:
        path = self._selected_path()
        rows = self._raw_rows.get(path, [])
        if not rows:
            return (), []

        has_header = self.header_checkbox.isChecked()
        if has_header:
            columns = tuple(cell.strip().lower() for cell in rows[0])
            data_rows = rows[1:]
        else:
            columns = tuple(f"__col_{index}__" for index in range(len(rows[0])))
            data_rows = rows
        return columns, data_rows

    def _refresh_column_options(self) -> None:
        path = self._selected_path()
        columns, data_rows = self._current_columns_and_rows()
        self._preview_columns = columns

        sample_first_row = data_rows[0] if data_rows else []
        combos = [
            self.date_combo,
            self.amount_combo,
            self.debit_combo,
            self.credit_combo,
            self.description_combo,
            self.memo_combo,
            self.id_combo,
            self.check_combo,
            self.currency_combo,
            self.account_id_combo,
            self.account_name_combo,
        ]
        previous_values = [combo.currentData() for combo in combos]
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", None)
            for index, column in enumerate(columns):
                sample_value = sample_first_row[index].strip() if index < len(sample_first_row) else ""
                combo.addItem(_column_label(column, sample_value), column)
            combo.blockSignals(False)

        for combo, value in zip(combos, previous_values):
            combo_index = combo.findData(value)
            if combo_index >= 0:
                combo.setCurrentIndex(combo_index)

        self.preview_table.setColumnCount(len(columns))
        self.preview_table.setHorizontalHeaderLabels([_column_label(column) for column in columns])
        sample_rows = data_rows[:10]
        self.preview_table.setRowCount(len(sample_rows))
        for row_index, row in enumerate(sample_rows):
            for col_index in range(len(columns)):
                text = row[col_index].strip() if col_index < len(row) else ""
                self.preview_table.setItem(row_index, col_index, QTableWidgetItem(text))
        self.preview_table.resizeColumnsToContents()

        existing_profile = self._profiles.get(path)
        if existing_profile is not None and existing_profile.has_header == self.header_checkbox.isChecked():
            self._apply_profile_to_widgets(existing_profile)

    def _apply_profile_to_widgets(self, profile: BankCsvProfile) -> None:
        widget_map = {
            self.date_combo: profile.date_column,
            self.amount_combo: profile.amount_column,
            self.debit_combo: profile.debit_column,
            self.credit_combo: profile.credit_column,
            self.description_combo: profile.description_column,
            self.memo_combo: profile.memo_column,
            self.id_combo: profile.id_column,
            self.check_combo: profile.check_number_column,
            self.currency_combo: profile.currency_column,
            self.account_id_combo: profile.account_id_column,
            self.account_name_combo: profile.account_name_column,
        }
        for combo, value in widget_map.items():
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

    def _build_profile(self) -> BankCsvProfile:
        return BankCsvProfile(
            has_header=self.header_checkbox.isChecked(),
            date_column=self.date_combo.currentData(),
            amount_column=self.amount_combo.currentData(),
            debit_column=self.debit_combo.currentData(),
            credit_column=self.credit_combo.currentData(),
            description_column=self.description_combo.currentData(),
            memo_column=self.memo_combo.currentData(),
            id_column=self.id_combo.currentData(),
            check_number_column=self.check_combo.currentData(),
            currency_column=self.currency_combo.currentData(),
            account_id_column=self.account_id_combo.currentData(),
            account_name_column=self.account_name_combo.currentData(),
        )

    def _reset_to_detected_profile(self) -> None:
        path = self._selected_path()
        if not path:
            return
        try:
            profile = suggest_bank_csv_profile(path)
        except Exception as exc:
            QMessageBox.warning(self, "CSV Error", str(exc))
            return

        self._profiles[path] = profile
        self.header_checkbox.setChecked(profile.has_header)
        self._refresh_column_options()
        self._apply_profile_to_widgets(profile)

    def _save_current_profile(self) -> None:
        path = self._last_path
        if not path or path not in self._raw_rows:
            return
        profile = self._build_profile()
        if profile.date_column and (profile.amount_column or profile.debit_column or profile.credit_column):
            self._profiles[path] = profile

    def _accept(self) -> None:
        self._save_current_profile()
        path = self._selected_path()
        if not path:
            self.accept()
            return

        profile = self._build_profile()
        if not profile.date_column:
            QMessageBox.warning(self, "Missing Column", "Select a date column.")
            return
        if not profile.amount_column and not (profile.debit_column or profile.credit_column):
            QMessageBox.warning(
                self,
                "Missing Amount",
                "Select an amount column or a debit/credit column pair.",
            )
            return

        self._profiles[path] = profile
        self.accept()

    def profiles(self) -> dict[str, BankCsvProfile]:
        return dict(self._profiles)
