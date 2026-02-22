from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QFileDialog,
    QCheckBox,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class InputsTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        self.etsy_statement_label = QLabel("(none)")
        self.etsy_soldorders_label = QLabel("(none)")
        self.ebay_label = QLabel("(none)")

        layout.addLayout(
            self._file_row(
                "Select Etsy Statement...",
                self.etsy_statement_label,
                "etsy_statement_path",
            )
        )
        layout.addLayout(
            self._file_row(
                "Select Etsy SoldOrders...",
                self.etsy_soldorders_label,
                "etsy_sold_orders_path",
            )
        )
        layout.addLayout(self._file_row("Select eBay Report...", self.ebay_label, "ebay_report_path"))

        self.use_range_checkbox = QCheckBox("Use date range filter")
        self.use_range_checkbox.toggled.connect(self._on_inputs_changed)
        layout.addWidget(self.use_range_checkbox)

        form = QFormLayout()
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addMonths(-1))
        self.start_date_edit.dateChanged.connect(self._on_inputs_changed)

        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.dateChanged.connect(self._on_inputs_changed)

        form.addRow("Start date", self.start_date_edit)
        form.addRow("End date", self.end_date_edit)
        layout.addLayout(form)

        layout.addStretch()

    def refresh_from_state(self) -> None:
        inputs = self.app_state.get("inputs", {})

        self.etsy_statement_label.setText(inputs.get("etsy_statement_path") or "(none)")
        self.etsy_soldorders_label.setText(inputs.get("etsy_sold_orders_path") or "(none)")
        self.ebay_label.setText(inputs.get("ebay_report_path") or "(none)")

        use_range = bool(inputs.get("use_date_range", False))
        self.use_range_checkbox.setChecked(use_range)

        start_iso = inputs.get("start_date")
        end_iso = inputs.get("end_date")

        if start_iso:
            start = date.fromisoformat(start_iso)
            self.start_date_edit.setDate(QDate(start.year, start.month, start.day))
        if end_iso:
            end = date.fromisoformat(end_iso)
            self.end_date_edit.setDate(QDate(end.year, end.month, end.day))

        self.start_date_edit.setEnabled(use_range)
        self.end_date_edit.setEnabled(use_range)

    def _file_row(self, button_text: str, label: QLabel, key: str) -> QHBoxLayout:
        row = QHBoxLayout()

        button = QPushButton(button_text)
        button.clicked.connect(lambda: self._select_file(label, key))

        row.addWidget(button)
        row.addWidget(label)

        return row

    def _select_file(self, label: QLabel, key: str) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Select CSV File", "", "CSV Files (*.csv)")
        if not file_path:
            return

        label.setText(file_path)
        inputs = dict(self.app_state.get("inputs", {}))
        inputs[key] = file_path
        self.app_state["inputs"] = inputs
        self._persist_inputs()

    def _on_inputs_changed(self) -> None:
        self.start_date_edit.setEnabled(self.use_range_checkbox.isChecked())
        self.end_date_edit.setEnabled(self.use_range_checkbox.isChecked())

        inputs = dict(self.app_state.get("inputs", {}))
        inputs["use_date_range"] = self.use_range_checkbox.isChecked()
        inputs["start_date"] = self.start_date_edit.date().toPython().isoformat()
        inputs["end_date"] = self.end_date_edit.date().toPython().isoformat()
        self.app_state["inputs"] = inputs
        self._persist_inputs()

    def _persist_inputs(self) -> None:
        book_id = self.app_state.get("book_id")
        if not book_id:
            return
        self.app_state["config_store"].save_inputs(book_id, self.app_state["inputs"])
        self.app_state["notify_state_changed"]()
