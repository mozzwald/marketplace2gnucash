from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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

_ETSY_STATEMENT_MONTH_RE = re.compile(r"etsy_statement_(\d{4})_(\d{1,2})\.csv$", re.IGNORECASE)
_ETSY_SOLD_ORDERS_MONTH_RE = re.compile(r"EtsySoldOrders(\d{4})-(\d{1,2})\.csv$", re.IGNORECASE)


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
        self.bank_imports_table.setHorizontalHeaderLabels(["Account", "Statement Directory", "Actions"])
        self.bank_imports_table.verticalHeader().setVisible(False)
        bank_layout.addWidget(self.bank_imports_table)

        self.bank_hint_label = QLabel(
            "Create one import bundle per bank/card account, then select a directory containing CSV or OFX/QFX statement files. Use CSV Mapping for headerless or nonstandard CSV layouts."
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
            etsy_monthly_exports = self._normalized_etsy_monthly_exports(item)
            normalized.append(
                {
                    "import_id": import_id,
                    "marketplace": marketplace,
                    "account_key": account_key,
                    "account_label": account_label,
                    "etsy_statement_path": item.get("etsy_statement_path") if isinstance(item.get("etsy_statement_path"), str) else None,
                    "etsy_sold_orders_path": item.get("etsy_sold_orders_path") if isinstance(item.get("etsy_sold_orders_path"), str) else None,
                    "etsy_monthly_exports": etsy_monthly_exports,
                    "ebay_report_path": item.get("ebay_report_path") if isinstance(item.get("ebay_report_path"), str) else None,
                    "ebay_report_directory": item.get("ebay_report_directory") if isinstance(item.get("ebay_report_directory"), str) else None,
                }
            )
        return normalized

    def _normalized_etsy_monthly_exports(self, item: dict[str, object]) -> list[dict[str, str | None]]:
        raw_exports = item.get("etsy_monthly_exports")
        exports: list[dict[str, str | None]] = []
        if isinstance(raw_exports, list):
            for raw_export in raw_exports:
                if not isinstance(raw_export, dict):
                    continue
                statement_path = raw_export.get("statement_path")
                sold_orders_path = raw_export.get("sold_orders_path")
                exports.append(
                    {
                        "statement_path": statement_path if isinstance(statement_path, str) and statement_path else None,
                        "sold_orders_path": sold_orders_path if isinstance(sold_orders_path, str) and sold_orders_path else None,
                    }
                )
        if exports:
            return exports

        statement_path = item.get("etsy_statement_path")
        sold_orders_path = item.get("etsy_sold_orders_path")
        if isinstance(statement_path, str) or isinstance(sold_orders_path, str):
            return [
                {
                    "statement_path": statement_path if isinstance(statement_path, str) and statement_path else None,
                    "sold_orders_path": sold_orders_path if isinstance(sold_orders_path, str) and sold_orders_path else None,
                }
            ]
        return []

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
                "etsy_monthly_exports": [],
                "ebay_report_path": None,
                "ebay_report_directory": None,
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
                file_parts = self._etsy_file_parts(marketplace_import)
            else:
                report_directory = marketplace_import.get("ebay_report_directory")
                if isinstance(report_directory, str) and report_directory:
                    file_parts = [self._ebay_directory_label(report_directory)]
                else:
                    report_path = marketplace_import.get("ebay_report_path")
                    file_parts = [f"Legacy report: {Path(report_path).name}" if report_path else "Report directory: (none)"]
            self.marketplace_imports_table.setItem(row_index, 2, QTableWidgetItem("\n".join(file_parts)))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            if marketplace == "etsy":
                add_pair_button = QPushButton("Add Month Pair")
                add_pair_button.clicked.connect(
                    lambda _checked=False, idx=row_index: self._add_etsy_monthly_pair(idx)
                )
                detect_button = QPushButton("Detect Directory")
                detect_button.clicked.connect(
                    lambda _checked=False, idx=row_index: self._detect_etsy_monthly_pairs(idx)
                )
                remove_pair_button = QPushButton("Remove Month")
                remove_pair_button.clicked.connect(
                    lambda _checked=False, idx=row_index: self._remove_etsy_monthly_pair(idx)
                )
                actions_layout.addWidget(add_pair_button)
                actions_layout.addWidget(detect_button)
                actions_layout.addWidget(remove_pair_button)
            else:
                report_button = QPushButton("Report Directory")
                report_button.clicked.connect(
                    lambda _checked=False, idx=row_index: self._select_ebay_report_directory(idx)
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

    def _etsy_file_parts(self, marketplace_import: dict[str, object]) -> list[str]:
        exports = [
            export
            for export in marketplace_import.get("etsy_monthly_exports", [])
            if isinstance(export, dict)
        ]
        if not exports:
            return ["(no monthly exports selected)"]
        parts: list[str] = []
        for index, export in enumerate(exports, start=1):
            statement_path = export.get("statement_path")
            sold_orders_path = export.get("sold_orders_path")
            statement_label = Path(statement_path).name if isinstance(statement_path, str) and statement_path else "(missing statement)"
            sold_label = Path(sold_orders_path).name if isinstance(sold_orders_path, str) and sold_orders_path else "(missing SoldOrders)"
            month = self._etsy_month_label(statement_path if isinstance(statement_path, str) else None) or self._etsy_month_label(sold_orders_path if isinstance(sold_orders_path, str) else None)
            prefix = month or f"Pair {index}"
            parts.append(f"{prefix}: {statement_label} / {sold_label}")
        return parts

    def _etsy_month_label(self, path: str | None) -> str | None:
        if not path:
            return None
        name = Path(path).name
        statement_match = _ETSY_STATEMENT_MONTH_RE.match(name)
        if statement_match:
            return f"{statement_match.group(1)}-{int(statement_match.group(2)):02d}"
        sold_match = _ETSY_SOLD_ORDERS_MONTH_RE.match(name)
        if sold_match:
            return f"{sold_match.group(1)}-{int(sold_match.group(2)):02d}"
        return None

    def _ebay_report_paths_in_directory(self, directory: str) -> list[str]:
        directory_path = Path(directory)
        if not directory_path.is_dir():
            return []
        return [
            str(path)
            for path in sorted(directory_path.iterdir(), key=lambda value: (value.name.lower(), str(value)))
            if path.is_file() and path.suffix.lower() == ".csv"
        ]

    def _ebay_directory_label(self, directory: str) -> str:
        if not Path(directory).is_dir():
            return f"Report directory: {Path(directory).name or directory} (missing)"
        paths = self._ebay_report_paths_in_directory(directory)
        return f"Report directory: {Path(directory).name or directory} ({len(paths)} CSV file(s))"

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

    def _select_ebay_report_directory(self, row_index: int) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        directory = QFileDialog.getExistingDirectory(self, "Select eBay Report Directory", "")
        if not directory:
            return
        marketplace_imports[row_index]["ebay_report_directory"] = directory
        marketplace_imports[row_index]["ebay_report_path"] = None
        self._set_marketplace_imports(marketplace_imports)

    def _add_etsy_monthly_pair(self, row_index: int) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        statement_path, _ = QFileDialog.getOpenFileName(self, "Select Etsy Statement CSV", "", "CSV Files (*.csv)")
        if not statement_path:
            return
        sold_orders_path, _ = QFileDialog.getOpenFileName(self, "Select Etsy SoldOrders CSV", str(Path(statement_path).parent), "CSV Files (*.csv)")
        if not sold_orders_path:
            return
        exports = list(marketplace_imports[row_index].get("etsy_monthly_exports", []))
        exports.append({"statement_path": statement_path, "sold_orders_path": sold_orders_path})
        marketplace_imports[row_index]["etsy_monthly_exports"] = exports
        self._sync_legacy_etsy_paths(marketplace_imports[row_index])
        self._set_marketplace_imports(marketplace_imports)

    def _detect_etsy_monthly_pairs(self, row_index: int) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        directory = QFileDialog.getExistingDirectory(self, "Select Etsy Export Directory", "")
        if not directory:
            return
        exports, warnings = self._detect_etsy_exports_in_directory(Path(directory))
        if not exports:
            QMessageBox.warning(self, "No Etsy Exports", "No matching Etsy monthly export files were found.")
            return
        marketplace_imports[row_index]["etsy_monthly_exports"] = exports
        self._sync_legacy_etsy_paths(marketplace_imports[row_index])
        self._set_marketplace_imports(marketplace_imports)
        if warnings:
            QMessageBox.warning(self, "Etsy Export Warnings", "\n".join(warnings))

    def _detect_etsy_exports_in_directory(self, directory: Path) -> tuple[list[dict[str, str | None]], list[str]]:
        statements: dict[str, str] = {}
        sold_orders: dict[str, str] = {}
        for path in directory.iterdir():
            if not path.is_file():
                continue
            statement_match = _ETSY_STATEMENT_MONTH_RE.match(path.name)
            if statement_match:
                statements[f"{statement_match.group(1)}-{int(statement_match.group(2)):02d}"] = str(path)
                continue
            sold_match = _ETSY_SOLD_ORDERS_MONTH_RE.match(path.name)
            if sold_match:
                sold_orders[f"{sold_match.group(1)}-{int(sold_match.group(2)):02d}"] = str(path)

        exports: list[dict[str, str | None]] = []
        warnings: list[str] = []
        for month in sorted(set(statements) | set(sold_orders)):
            statement_path = statements.get(month)
            sold_orders_path = sold_orders.get(month)
            if not statement_path:
                warnings.append(f"{month}: SoldOrders found but statement CSV is missing")
            if not sold_orders_path:
                warnings.append(f"{month}: statement CSV found but SoldOrders CSV is missing")
            exports.append({"statement_path": statement_path, "sold_orders_path": sold_orders_path})
        return exports, warnings

    def _remove_etsy_monthly_pair(self, row_index: int) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        exports = [
            export
            for export in marketplace_imports[row_index].get("etsy_monthly_exports", [])
            if isinstance(export, dict)
        ]
        if not exports:
            return
        labels = self._etsy_file_parts(marketplace_imports[row_index])
        selected, accepted = QInputDialog.getItem(self, "Remove Etsy Month", "Monthly export", labels, 0, False)
        if not accepted:
            return
        selected_index = labels.index(selected)
        del exports[selected_index]
        marketplace_imports[row_index]["etsy_monthly_exports"] = exports
        self._sync_legacy_etsy_paths(marketplace_imports[row_index])
        self._set_marketplace_imports(marketplace_imports)

    def _sync_legacy_etsy_paths(self, marketplace_import: dict[str, object]) -> None:
        exports = [
            export
            for export in marketplace_import.get("etsy_monthly_exports", [])
            if isinstance(export, dict)
        ]
        first = exports[0] if exports else {}
        marketplace_import["etsy_statement_path"] = first.get("statement_path") if isinstance(first.get("statement_path"), str) else None
        marketplace_import["etsy_sold_orders_path"] = first.get("sold_orders_path") if isinstance(first.get("sold_orders_path"), str) else None

    def _clear_marketplace_files(self, row_index: int) -> None:
        marketplace_imports = self._marketplace_imports()
        if row_index >= len(marketplace_imports):
            return
        marketplace = marketplace_imports[row_index]["marketplace"]
        if marketplace == "etsy":
            marketplace_imports[row_index]["etsy_statement_path"] = None
            marketplace_imports[row_index]["etsy_sold_orders_path"] = None
            marketplace_imports[row_index]["etsy_monthly_exports"] = []
        else:
            marketplace_imports[row_index]["ebay_report_path"] = None
            marketplace_imports[row_index]["ebay_report_directory"] = None
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
                    "statement_directory": item.get("statement_directory") if isinstance(item.get("statement_directory"), str) else None,
                    "statement_paths": [
                        path
                        for path in item.get("statement_paths", [])
                        if isinstance(path, str) and path
                    ],
                    "csv_profile": dict(item.get("csv_profile")) if isinstance(item.get("csv_profile"), dict) else None,
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

            statement_directory = bank_import.get("statement_directory")
            if isinstance(statement_directory, str) and statement_directory:
                files_label = self._bank_directory_label(statement_directory, bank_import)
            elif bank_import.get("statement_paths"):
                files_label = "(legacy file selection)"
            else:
                files_label = "(no directory selected)"
            self.bank_imports_table.setItem(row_index, 1, QTableWidgetItem(files_label))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)

            select_account_button = QPushButton("Select Account")
            select_account_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._select_bank_import_account(idx)
            )
            select_directory_button = QPushButton("Select Directory")
            select_directory_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._select_bank_import_directory(idx)
            )
            csv_profile_button = QPushButton("CSV Mapping")
            csv_profile_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._configure_csv_profiles(idx)
            )
            clear_files_button = QPushButton("Clear Directory")
            clear_files_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._clear_directory_for_bank_import(idx)
            )
            remove_button = QPushButton("Remove")
            remove_button.clicked.connect(
                lambda _checked=False, idx=row_index: self._remove_bank_import(idx)
            )

            actions_layout.addWidget(select_account_button)
            actions_layout.addWidget(select_directory_button)
            actions_layout.addWidget(csv_profile_button)
            actions_layout.addWidget(clear_files_button)
            actions_layout.addWidget(remove_button)
            actions_layout.addStretch()
            self.bank_imports_table.setCellWidget(row_index, 2, actions_widget)

        self.bank_imports_table.resizeColumnsToContents()

    def _add_bank_import(self) -> None:
        bank_imports = self._bank_imports()
        bank_imports.append({"account_guid": None, "statement_directory": None, "csv_profile": None})
        self._set_bank_imports(bank_imports)

    def _bank_statement_paths_in_directory(self, directory: str) -> list[str]:
        directory_path = Path(directory)
        if not directory_path.is_dir():
            return []
        supported = {".csv", ".ofx", ".qfx"}
        return [
            str(path)
            for path in sorted(directory_path.iterdir(), key=lambda value: (value.name.lower(), str(value)))
            if path.is_file() and path.suffix.lower() in supported
        ]

    def _bank_directory_label(self, directory: str, bank_import: dict[str, object]) -> str:
        suffix = " [mapped]" if isinstance(bank_import.get("csv_profile"), dict) else ""
        if not Path(directory).is_dir():
            return f"{Path(directory).name or directory}: missing directory{suffix}"
        paths = self._bank_statement_paths_in_directory(directory)
        return f"{Path(directory).name or directory}: {len(paths)} statement file(s){suffix}"

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

    def _select_bank_import_directory(self, row_index: int) -> None:
        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Bank/Card Statement Directory",
            "",
        )
        if not directory:
            return

        previous_directory = bank_imports[row_index].get("statement_directory")
        bank_imports[row_index]["statement_directory"] = directory
        bank_imports[row_index]["statement_paths"] = []
        if previous_directory != directory:
            bank_imports[row_index]["csv_profile"] = None
        bank_imports[row_index]["csv_profiles"] = {}
        self._set_bank_imports(bank_imports)

    def _configure_csv_profiles(self, row_index: int) -> None:
        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return

        statement_directory = bank_imports[row_index].get("statement_directory")
        if not isinstance(statement_directory, str) or not statement_directory:
            QMessageBox.information(
                self,
                "No Directory",
                "Select a statement directory for this account import bundle first.",
            )
            return

        statement_paths = [
            path
            for path in self._bank_statement_paths_in_directory(statement_directory)
            if path.lower().endswith(".csv")
        ]
        if not statement_paths:
            QMessageBox.information(
                self,
                "No CSV Files",
                "The selected statement directory does not contain any CSV files.",
            )
            return

        csv_profile = bank_imports[row_index].get("csv_profile")
        existing_profiles = {
            path: csv_profile
            for path in statement_paths
            if isinstance(csv_profile, dict)
        }
        dialog = CsvProfileDialog(
            statement_paths,
            existing_profiles,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        profiles = dialog.profiles()
        first_profile = profiles.get(statement_paths[0])
        if first_profile is None and profiles:
            first_profile = next(iter(profiles.values()))
        bank_imports[row_index]["csv_profile"] = (
            bank_csv_profile_to_dict(first_profile) if first_profile is not None else None
        )
        bank_imports[row_index]["csv_profiles"] = {}
        self._set_bank_imports(bank_imports)

    def _clear_directory_for_bank_import(self, row_index: int) -> None:
        bank_imports = self._bank_imports()
        if row_index >= len(bank_imports):
            return
        bank_imports[row_index]["statement_directory"] = None
        bank_imports[row_index]["statement_paths"] = []
        bank_imports[row_index]["csv_profile"] = None
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
