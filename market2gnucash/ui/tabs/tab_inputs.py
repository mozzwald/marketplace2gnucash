from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.models import AccountRecord
from market2gnucash.core.parsers import bank_csv_profile_to_dict
from market2gnucash.ui.account_picker import AccountPickerDialog
from market2gnucash.ui.csv_profile_dialog import CsvProfileDialog


class InputsTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        date_group = QGroupBox("Date Range Filter")
        date_layout = QVBoxLayout(date_group)

        self.use_range_checkbox = QCheckBox("Use date range filter")
        self.use_range_checkbox.toggled.connect(self._on_inputs_changed)
        date_layout.addWidget(self.use_range_checkbox)

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
        date_layout.addLayout(form)
        layout.addWidget(date_group)

        layout.addWidget(self._separator())

        marketplace_group = QGroupBox("Marketplace Accounts")
        marketplace_layout = QVBoxLayout(marketplace_group)

        marketplace_tools = QHBoxLayout()
        add_etsy_button = QPushButton("Add Etsy Account")
        add_etsy_button.clicked.connect(lambda: self._add_marketplace_import("etsy"))
        add_ebay_button = QPushButton("Add eBay Account")
        add_ebay_button.clicked.connect(lambda: self._add_marketplace_import("ebay"))
        marketplace_tools.addWidget(add_etsy_button)
        marketplace_tools.addWidget(add_ebay_button)
        marketplace_tools.addStretch()
        marketplace_layout.addLayout(marketplace_tools)

        self.marketplace_imports_table = QTableWidget()
        self.marketplace_imports_table.setColumnCount(4)
        self.marketplace_imports_table.setHorizontalHeaderLabels(
            ["Marketplace", "Account Name", "Files", "Actions"]
        )
        self.marketplace_imports_table.verticalHeader().setVisible(False)
        marketplace_layout.addWidget(self.marketplace_imports_table)

        self.marketplace_hint_label = QLabel(
            "Create one import bundle per Etsy or eBay seller account. Etsy requires Statement and Sold Orders; eBay requires Transaction Report."
        )
        marketplace_layout.addWidget(self.marketplace_hint_label)
        layout.addWidget(marketplace_group)

        layout.addWidget(self._separator())

        bank_group = QGroupBox("Bank / Card Account Imports")
        bank_layout = QVBoxLayout(bank_group)

        bank_tools = QHBoxLayout()
        add_import_button = QPushButton("Add Account Import...")
        add_import_button.clicked.connect(self._add_bank_import)
        bank_tools.addWidget(add_import_button)
        bank_tools.addStretch()
        bank_layout.addLayout(bank_tools)

        self.bank_imports_table = QTableWidget()
        self.bank_imports_table.setColumnCount(3)
        self.bank_imports_table.setHorizontalHeaderLabels(["Account", "Statement Files", "Actions"])
        self.bank_imports_table.verticalHeader().setVisible(False)
        bank_layout.addWidget(self.bank_imports_table)

        self.bank_hint_label = QLabel(
            "Create one import bundle per bank/card account, then attach one or more CSV or OFX/QFX statement files. Use CSV Mapping for headerless or nonstandard CSV layouts."
        )
        bank_layout.addWidget(self.bank_hint_label)
        layout.addWidget(bank_group)

        layout.addStretch()

    def refresh_from_state(self) -> None:
        inputs = self.app_state.get("inputs", {})

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

        self._populate_marketplace_imports_table()
        self._populate_bank_imports_table()

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def _marketplace_imports(self) -> list[dict[str, object]]:
        inputs = self.app_state.get("inputs", {})
        imports = inputs.get("marketplace_imports", [])
        normalized: list[dict[str, object]] = []
        for item in imports:
            if not isinstance(item, dict):
                continue
            marketplace = item.get("marketplace") if isinstance(item.get("marketplace"), str) else None
            account_key = item.get("account_key") if isinstance(item.get("account_key"), str) else None
            import_id = item.get("import_id") if isinstance(item.get("import_id"), str) else None
            account_label = item.get("account_label") if isinstance(item.get("account_label"), str) else ""
            if not marketplace or marketplace not in {"etsy", "ebay"} or not account_key or not import_id:
                continue
            normalized.append(
                {
                    "import_id": import_id,
                    "marketplace": marketplace,
                    "account_key": account_key,
                    "account_label": account_label,
                    "etsy_statement_path": item.get("etsy_statement_path") if isinstance(item.get("etsy_statement_path"), str) else None,
                    "etsy_sold_orders_path": item.get("etsy_sold_orders_path") if isinstance(item.get("etsy_sold_orders_path"), str) else None,
                    "ebay_report_path": item.get("ebay_report_path") if isinstance(item.get("ebay_report_path"), str) else None,
                }
            )
        return normalized

    def _set_marketplace_imports(self, imports: list[dict[str, object]]) -> None:
        inputs = dict(self.app_state.get("inputs", {}))
        inputs["marketplace_imports"] = imports
        self.app_state["inputs"] = inputs
        self._persist_inputs()

    def _add_marketplace_import(self, marketplace: str) -> None:
        marketplace_imports = self._marketplace_imports()
        marketplace_imports.append(
            {
                "import_id": uuid4().hex,
                "marketplace": marketplace,
                "account_key": f"{marketplace}:{uuid4().hex[:8]}",
                "account_label": f"{'Etsy' if marketplace == 'etsy' else 'eBay'} Account {len(marketplace_imports) + 1}",
                "etsy_statement_path": None,
                "etsy_sold_orders_path": None,
                "ebay_report_path": None,
            }
        )
        self._set_marketplace_imports(marketplace_imports)

    def _populate_marketplace_imports_table(self) -> None:
        marketplace_imports = self._marketplace_imports()
        self.marketplace_imports_table.setRowCount(len(marketplace_imports))
        for row_index, marketplace_import in enumerate(marketplace_imports):
            marketplace = str(marketplace_import["marketplace"])
            title = "Etsy" if marketplace == "etsy" else "eBay"
            self.marketplace_imports_table.setItem(row_index, 0, QTableWidgetItem(title))

            name_edit = QLineEdit(str(marketplace_import.get("account_label") or ""))
            name_edit.editingFinished.connect(
                lambda idx=row_index, widget=name_edit: self._rename_marketplace_import(idx, widget.text())
            )
            self.marketplace_imports_table.setCellWidget(row_index, 1, name_edit)

            if marketplace == "etsy":
                file_parts = [
                    f"Statement: {Path(path).name}" if path else "Statement: (none)"
                    for path in [marketplace_import.get("etsy_statement_path")]
                ]
                file_parts.extend(
                    f"Sold Orders: {Path(path).name}" if path else "Sold Orders: (none)"
                    for path in [marketplace_import.get("etsy_sold_orders_path")]
                )
            else:
                report_path = marketplace_import.get("ebay_report_path")
                file_parts = [f"Report: {Path(report_path).name}" if report_path else "Report: (none)"]
            self.marketplace_imports_table.setItem(row_index, 2, QTableWidgetItem("\n".join(file_parts)))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            if marketplace == "etsy":
                statement_button = QPushButton("Statement CSV")
                statement_button.clicked.connect(
                    lambda _checked=False, idx=row_index: self._select_marketplace_file(idx, "etsy_statement_path", "Select Etsy Statement CSV")
                )
                sold_orders_button = QPushButton("Sold Orders CSV")
                sold_orders_button.clicked.connect(
                    lambda _checked=False, idx=row_index: self._select_marketplace_file(idx, "etsy_sold_orders_path", "Select Etsy Sold Orders CSV")
                )
                actions_layout.addWidget(statement_button)
                actions_layout.addWidget(sold_orders_button)
            else:
                report_button = QPushButton("Report CSV")
                report_button.clicked.connect(
                    lambda _checked=False, idx=row_index: self._select_marketplace_file(idx, "ebay_report_path", "Select eBay Transaction Report CSV")
                )
                actions_layout.addWidget(report_button)

            clear_files_button = QPushButton("Clear Files")
            clear_files_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._clear_marketplace_files(idx)
            )
            remove_button = QPushButton("Remove")
            remove_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._remove_marketplace_import(idx)
            )
            actions_layout.addWidget(clear_files_button)
            actions_layout.addWidget(remove_button)
            actions_layout.addStretch()
            self.marketplace_imports_table.setCellWidget(row_index, 3, actions_widget)

        self.marketplace_imports_table.resizeColumnsToContents()

    def _rename_marketplace_import(self, row_index: int, account_label: str) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        trimmed = account_label.strip()
        if not trimmed:
            return
        marketplace_imports[row_index]["account_label"] = trimmed
        self._set_marketplace_imports(marketplace_imports)

    def _select_marketplace_file(self, row_index: int, field_name: str, title: str) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        file_path, _ = QFileDialog.getOpenFileName(self, title, "", "CSV Files (*.csv)")
        if not file_path:
            return
        marketplace_imports[row_index][field_name] = file_path
        self._set_marketplace_imports(marketplace_imports)

    def _clear_marketplace_files(self, row_index: int) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        marketplace = marketplace_imports[row_index]["marketplace"]
        if marketplace == "etsy":
            marketplace_imports[row_index]["etsy_statement_path"] = None
            marketplace_imports[row_index]["etsy_sold_orders_path"] = None
        else:
            marketplace_imports[row_index]["ebay_report_path"] = None
        self._set_marketplace_imports(marketplace_imports)

    def _remove_marketplace_import(self, row_index: int) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        del marketplace_imports[row_index]
        self._set_marketplace_imports(marketplace_imports)

    def _bank_imports(self) -> list[dict[str, object]]:
        inputs = self.app_state.get("inputs", {})
        imports = inputs.get("bank_imports", [])
        normalized: list[dict[str, object]] = []
        for item in imports:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "account_guid": item.get("account_guid") if isinstance(item.get("account_guid"), str) else None,
                    "statement_paths": [
                        path
                        for path in item.get("statement_paths", [])
                        if isinstance(path, str) and path
                    ],
                    "csv_profiles": {
                        path: dict(profile)
                        for path, profile in item.get("csv_profiles", {}).items()
                        if isinstance(path, str) and isinstance(profile, dict)
                    },
                }
            )
        return normalized

    def _set_bank_imports(self, imports: list[dict[str, object]]) -> None:
        inputs = dict(self.app_state.get("inputs", {}))
        inputs["bank_imports"] = imports
        self.app_state["inputs"] = inputs
        self._persist_inputs()

    def _populate_bank_imports_table(self) -> None:
        bank_imports = self._bank_imports()
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})

        self.bank_imports_table.setRowCount(len(bank_imports))
        for row_index, bank_import in enumerate(bank_imports):
            account_guid = bank_import.get("account_guid")
            account = accounts_by_guid.get(account_guid) if isinstance(account_guid, str) else None
            account_label = account.full_name if account else "(select account)"
            self.bank_imports_table.setItem(row_index, 0, QTableWidgetItem(account_label))

            statement_paths = [
                str(path) for path in bank_import.get("statement_paths", []) if isinstance(path, str)
            ]
            csv_profiles = bank_import.get("csv_profiles", {})
            if statement_paths:
                labels = []
                for path in statement_paths:
                    suffix = " [mapped]" if isinstance(csv_profiles, dict) and path in csv_profiles else ""
                    labels.append(f"{Path(path).name}{suffix}")
                files_label = "\n".join(labels)
            else:
                files_label = "(no files selected)"
            self.bank_imports_table.setItem(row_index, 1, QTableWidgetItem(files_label))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)

            select_account_button = QPushButton("Select Account")
            select_account_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._select_bank_import_account(idx)
            )
            add_files_button = QPushButton("Add Files")
            add_files_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._add_files_to_bank_import(idx)
            )
            csv_profile_button = QPushButton("CSV Mapping")
            csv_profile_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._configure_csv_profiles(idx)
            )
            clear_files_button = QPushButton("Clear Files")
            clear_files_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._clear_files_for_bank_import(idx)
            )
            remove_button = QPushButton("Remove")
            remove_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._remove_bank_import(idx)
            )

            actions_layout.addWidget(select_account_button)
            actions_layout.addWidget(add_files_button)
            actions_layout.addWidget(csv_profile_button)
            actions_layout.addWidget(clear_files_button)
            actions_layout.addWidget(remove_button)
            actions_layout.addStretch()
            self.bank_imports_table.setCellWidget(row_index, 2, actions_widget)

        self.bank_imports_table.resizeColumnsToContents()

    def _add_bank_import(self) -> None:
        bank_imports = self._bank_imports()
        bank_imports.append({"account_guid": None, "statement_paths": [], "csv_profiles": {}})
        self._set_bank_imports(bank_imports)

    def _select_bank_import_account(self, row_index: int) -> None:
        accounts: tuple[AccountRecord, ...] = self.app_state.get("accounts", ())
        if not accounts:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return

        current_guid = bank_imports[row_index].get("account_guid")
        dialog = AccountPickerDialog(
            accounts,
            selected_guid=current_guid if isinstance(current_guid, str) else None,
            allowed_types={"ASSET", "BANK", "CASH", "CREDIT", "LIABILITY"},
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_guid = dialog.selected_guid()
        if not selected_guid:
            return

        bank_imports[row_index]["account_guid"] = selected_guid
        self._set_bank_imports(bank_imports)

    def _add_files_to_bank_import(self, row_index: int) -> None:
        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Bank/Card Statement Files",
            "",
            "Statement Files (*.csv *.ofx *.qfx);;CSV Files (*.csv);;OFX Files (*.ofx *.qfx);;All Files (*)",
        )
        if not file_paths:
            return

        existing = list(bank_imports[row_index].get("statement_paths", []))
        for path in file_paths:
            if path not in existing:
                existing.append(path)
        bank_imports[row_index]["statement_paths"] = existing
        profiles = bank_imports[row_index].get("csv_profiles", {})
        if isinstance(profiles, dict):
            bank_imports[row_index]["csv_profiles"] = {
                path: profile for path, profile in profiles.items() if path in existing
            }
        self._set_bank_imports(bank_imports)

    def _configure_csv_profiles(self, row_index: int) -> None:
        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return

        statement_paths = [
            path
            for path in bank_imports[row_index].get("statement_paths", [])
            if isinstance(path, str) and path.lower().endswith(".csv")
        ]
        if not statement_paths:
            QMessageBox.information(
                self,
                "No CSV Files",
                "Attach one or more CSV files to this account import bundle first.",
            )
            return

        csv_profiles = bank_imports[row_index].get("csv_profiles", {})
        dialog = CsvProfileDialog(
            statement_paths,
            csv_profiles if isinstance(csv_profiles, dict) else {},
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        bank_imports[row_index]["csv_profiles"] = {
            path: bank_csv_profile_to_dict(profile)
            for path, profile in dialog.profiles().items()
        }
        self._set_bank_imports(bank_imports)

    def _clear_files_for_bank_import(self, row_index: int) -> None:
        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return
        bank_imports[row_index]["statement_paths"] = []
        bank_imports[row_index]["csv_profiles"] = {}
        self._set_bank_imports(bank_imports)

    def _remove_bank_import(self, row_index: int) -> None:
        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return
        del bank_imports[row_index]
        self._set_bank_imports(bank_imports)

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
        if book_id:
            self.app_state["config_store"].save_inputs(book_id, self.app_state["inputs"])
        self.app_state["notify_state_changed"]()
