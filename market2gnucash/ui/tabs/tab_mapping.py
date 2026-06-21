from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
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

from market2gnucash.core.models import AccountRecord, MappingConfig, MarketplaceAccountMapping
from market2gnucash.core.parsers import parse_ebay_reports, parse_etsy_statement
from market2gnucash.core.rules import (
    ebay_mapping_key,
    ebay_standalone_fee_mapping_key,
    etsy_mapping_key,
)
from market2gnucash.ui.account_picker import AccountPickerDialog


class MappingTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Marketplace Account"))
        self.account_selector = QComboBox()
        self.account_selector.currentIndexChanged.connect(self.refresh_from_state)
        selector_row.addWidget(self.account_selector)
        selector_row.addStretch()
        layout.addLayout(selector_row)

        self.empty_label = QLabel("Add a marketplace account in the Inputs tab to configure mappings.")
        layout.addWidget(self.empty_label)

        account_group = QGroupBox("Marketplace Core Accounts")
        account_layout = QGridLayout(account_group)

        self.account_labels: dict[str, QLabel] = {}
        self.account_buttons: dict[str, QPushButton] = {}
        account_rows = [
            ("clearing_guid", "Clearing", {"ASSET", "BANK", "CASH"}),
            ("income_guid", "Sales Income", {"INCOME"}),
            ("refunds_guid", "Refunds Expense", {"EXPENSE"}),
        ]
        for row_index, (field_name, title, allowed_types) in enumerate(account_rows):
            label = QLabel("(not selected)")
            button = QPushButton(f"Select {title}")
            button.clicked.connect(
                lambda _checked=False, f=field_name, t=allowed_types: self._pick_core_account(f, t)
            )
            account_layout.addWidget(QLabel(title), row_index, 0)
            account_layout.addWidget(label, row_index, 1)
            account_layout.addWidget(button, row_index, 2)
            self.account_labels[field_name] = label
            self.account_buttons[field_name] = button
        layout.addWidget(account_group)
        self.account_group = account_group

        tools_row = QHBoxLayout()
        self.scan_button = QPushButton("Scan Inputs for Mapping Keys")
        self.scan_button.clicked.connect(self._scan_inputs)
        tools_row.addWidget(self.scan_button)
        tools_row.addStretch()
        layout.addLayout(tools_row)

        self.fee_label = QLabel("Marketplace Fee Mapping")
        layout.addWidget(self.fee_label)

        self.fee_table = QTableWidget()
        self.fee_table.setColumnCount(3)
        self.fee_table.setHorizontalHeaderLabels(["Mapping Key", "Account", "Action"])
        self.fee_table.verticalHeader().setVisible(False)
        layout.addWidget(self.fee_table)

    def refresh_from_state(self) -> None:
        marketplace_imports = self._marketplace_imports()
        selected_key = self._selected_account_key()

        self.account_selector.blockSignals(True)
        self.account_selector.clear()
        for marketplace_import in marketplace_imports:
            self.account_selector.addItem(
                f"{'Etsy' if marketplace_import['marketplace'] == 'etsy' else 'eBay'}: {marketplace_import['account_label']}",
                marketplace_import["account_key"],
            )
        if marketplace_imports:
            match_index = next(
                (
                    idx
                    for idx, marketplace_import in enumerate(marketplace_imports)
                    if marketplace_import["account_key"] == selected_key
                ),
                0,
            )
            self.account_selector.setCurrentIndex(match_index)
        self.account_selector.blockSignals(False)

        active_import = self._active_marketplace_import()
        has_active = active_import is not None
        self.empty_label.setVisible(not has_active)
        self.account_group.setVisible(has_active)
        self.scan_button.setEnabled(has_active)
        self.fee_label.setVisible(has_active)
        self.fee_table.setVisible(has_active)

        if not active_import:
            self.fee_table.setRowCount(0)
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        account_mapping = self._account_mapping(mapping, active_import)
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})

        for field_name, label in self.account_labels.items():
            guid = getattr(account_mapping, field_name)
            account = accounts_by_guid.get(guid) if guid else None
            label.setText(account.full_name if account else "(not selected)")

        mapping_keys = set(self.app_state.get("marketplace_mapping_keys", {}).get(str(active_import["account_key"]), ()))
        plan = self.app_state.get("plan_result")
        if plan is not None:
            mapping_keys.update(plan.marketplace_mapping_keys.get(str(active_import["account_key"]), ()))

        if active_import["marketplace"] == "etsy":
            self.fee_label.setText("Etsy Fee Mapping")
            self.fee_table.setHorizontalHeaderLabels(["Etsy Key", "Account", "Action"])
            self._populate_fee_table(sorted(mapping_keys), account_mapping)
        else:
            self.fee_label.setText("eBay Expense and Fee Mapping")
            self.fee_table.setHorizontalHeaderLabels(["eBay Mapping Key", "Account", "Action"])
            self._populate_fee_table(sorted(mapping_keys), account_mapping)

    def _marketplace_imports(self) -> list[dict[str, object]]:
        inputs = self.app_state.get("inputs", {})
        imports = inputs.get("marketplace_imports", [])
        normalized: list[dict[str, object]] = []
        for item in imports:
            if not isinstance(item, dict):
                continue
            marketplace = item.get("marketplace")
            account_key = item.get("account_key")
            account_label = item.get("account_label")
            if all(isinstance(value, str) and value for value in (marketplace, account_key, account_label)):
                etsy_monthly_exports: list[dict[str, str]] = []
                raw_exports = item.get("etsy_monthly_exports")
                if isinstance(raw_exports, list):
                    for raw_export in raw_exports:
                        if not isinstance(raw_export, dict):
                            continue
                        statement_path = raw_export.get("statement_path")
                        sold_orders_path = raw_export.get("sold_orders_path")
                        etsy_monthly_exports.append(
                            {
                                "statement_path": statement_path if isinstance(statement_path, str) else "",
                                "sold_orders_path": sold_orders_path if isinstance(sold_orders_path, str) else "",
                            }
                        )
                if not etsy_monthly_exports:
                    statement_path = item.get("etsy_statement_path")
                    sold_orders_path = item.get("etsy_sold_orders_path")
                    if isinstance(statement_path, str) or isinstance(sold_orders_path, str):
                        etsy_monthly_exports.append(
                            {
                                "statement_path": statement_path if isinstance(statement_path, str) else "",
                                "sold_orders_path": sold_orders_path if isinstance(sold_orders_path, str) else "",
                            }
                        )
                normalized.append(
                    {
                        "marketplace": marketplace,
                        "account_key": account_key,
                        "account_label": account_label,
                        "etsy_statement_path": item.get("etsy_statement_path") if isinstance(item.get("etsy_statement_path"), str) else "",
                        "etsy_monthly_exports": etsy_monthly_exports,
                        "ebay_report_path": item.get("ebay_report_path") if isinstance(item.get("ebay_report_path"), str) else "",
                        "ebay_report_directory": item.get("ebay_report_directory") if isinstance(item.get("ebay_report_directory"), str) else "",
                    }
                )
        return normalized

    def _ebay_report_paths_in_directory(self, directory: str) -> tuple[str, ...]:
        directory_path = Path(directory)
        if not directory_path.is_dir():
            return ()
        return tuple(
            str(path)
            for path in sorted(directory_path.iterdir(), key=lambda value: (value.name.lower(), str(value)))
            if path.is_file() and path.suffix.lower() == ".csv"
        )

    def _selected_account_key(self) -> str | None:
        value = self.account_selector.currentData()
        return value if isinstance(value, str) and value else None

    def _active_marketplace_import(self) -> dict[str, object] | None:
        selected_key = self._selected_account_key()
        for marketplace_import in self._marketplace_imports():
            if marketplace_import["account_key"] == selected_key:
                return marketplace_import
        imports = self._marketplace_imports()
        return imports[0] if imports else None

    def _account_mapping(
        self,
        mapping: MappingConfig,
        marketplace_import: dict[str, object],
    ) -> MarketplaceAccountMapping:
        return mapping.marketplace_accounts.get(
            str(marketplace_import["account_key"]),
            MarketplaceAccountMapping(
                marketplace=str(marketplace_import["marketplace"]),
                account_label=str(marketplace_import["account_label"]),
            ),
        )

    def _scan_inputs(self) -> None:
        inputs = self.app_state.get("inputs", {})
        use_range = bool(inputs.get("use_date_range"))
        start_date = date.fromisoformat(inputs["start_date"]) if use_range and inputs.get("start_date") else None
        end_date = date.fromisoformat(inputs["end_date"]) if use_range and inputs.get("end_date") else None

        mapping_keys: dict[str, tuple[str, ...]] = dict(self.app_state.get("marketplace_mapping_keys", {}))
        try:
            for marketplace_import in self._marketplace_imports():
                found_keys: set[str] = set()
                if marketplace_import["marketplace"] == "etsy":
                    exports = marketplace_import.get("etsy_monthly_exports", [])
                    if isinstance(exports, list):
                        for export in exports:
                            if not isinstance(export, dict):
                                continue
                            statement_path = export.get("statement_path")
                            if not isinstance(statement_path, str) or not statement_path:
                                continue
                            if not Path(statement_path).is_file():
                                raise ValueError(
                                    f"Etsy import '{marketplace_import['account_label']}' references a missing statement CSV: {statement_path}. Reselect the file or restore it before scanning."
                                )
                            statement_rows = parse_etsy_statement(
                                statement_path,
                                start_date,
                                end_date,
                            )
                            for row in statement_rows:
                                if row.row_type != "Fee":
                                    continue
                                found_keys.add(etsy_mapping_key(row))
                                if row.title.startswith("Transaction fee:") and row.title != "Transaction fee: Shipping":
                                    found_keys.add("etsy:Fee:Transaction fee:*")
                if marketplace_import["marketplace"] == "ebay":
                    report_directory = marketplace_import.get("ebay_report_directory")
                    if isinstance(report_directory, str) and report_directory:
                        if not Path(report_directory).is_dir():
                            raise ValueError(
                                f"eBay import '{marketplace_import['account_label']}' references a missing report directory: {report_directory}. Reselect the directory or restore it before scanning."
                            )
                        report_paths = self._ebay_report_paths_in_directory(report_directory)
                        if not report_paths:
                            raise ValueError(
                                f"eBay import '{marketplace_import['account_label']}' report directory has no CSV files: {report_directory}"
                            )
                    elif marketplace_import["ebay_report_path"]:
                        if not Path(str(marketplace_import["ebay_report_path"])).is_file():
                            raise ValueError(
                                f"eBay import '{marketplace_import['account_label']}' references a missing transaction report CSV: {marketplace_import['ebay_report_path']}. Reselect the file or restore it before scanning."
                            )
                        report_paths = (str(marketplace_import["ebay_report_path"]),)
                    else:
                        report_paths = ()
                    if not report_paths:
                        continue
                    ebay_data = parse_ebay_reports(
                        report_paths,
                        start_date,
                        end_date,
                    )
                    found_keys.update(ebay_mapping_key(column) for column in ebay_data.fee_columns)
                    for row_type in ("Other fee", "Shipping label"):
                        if any(row.row_type == row_type for row in ebay_data.report_rows):
                            found_keys.add(ebay_standalone_fee_mapping_key(row_type))
                mapping_keys[str(marketplace_import["account_key"])] = tuple(sorted(found_keys))
        except Exception as exc:
            QMessageBox.critical(self, "Scan failed", str(exc))
            return

        self.app_state["marketplace_mapping_keys"] = mapping_keys
        self.app_state["notify_state_changed"]()

    def _pick_core_account(self, field_name: str, allowed_types: set[str]) -> None:
        active_import = self._active_marketplace_import()
        if not active_import:
            return

        accounts: tuple[AccountRecord, ...] = self.app_state.get("accounts", ())
        if not accounts:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        current_guid = getattr(self._account_mapping(mapping, active_import), field_name)

        dialog = AccountPickerDialog(
            accounts,
            selected_guid=current_guid,
            allowed_types=allowed_types,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_guid = dialog.selected_guid()
        if not selected_guid:
            return

        current_account_mapping = self._account_mapping(mapping, active_import)
        updated_account_mapping = replace(current_account_mapping, **{field_name: selected_guid})
        self._save_account_mapping(str(active_import["account_key"]), updated_account_mapping)

    def _populate_fee_table(self, keys: list[str], account_mapping: MarketplaceAccountMapping) -> None:
        self.fee_table.setRowCount(len(keys))
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})

        for row_index, key in enumerate(keys):
            if key.startswith("ebay:fee_col:"):
                display_key = key.removeprefix("ebay:fee_col:")
            elif key.startswith("ebay:standalone_fee:"):
                display_key = f"Standalone {key.removeprefix('ebay:standalone_fee:')}"
            else:
                display_key = key
            self.fee_table.setItem(row_index, 0, QTableWidgetItem(display_key))

            guid = account_mapping.fee_accounts.get(key)
            account = accounts_by_guid.get(guid) if guid else None
            account_label = account.full_name if account else "(unmapped)"
            self.fee_table.setItem(row_index, 1, QTableWidgetItem(account_label))

            button = QPushButton("Select")
            button.clicked.connect(lambda _checked=False, mapping_key=key: self._pick_fee_mapping_account(mapping_key))
            self.fee_table.setCellWidget(row_index, 2, button)

        self.fee_table.resizeColumnsToContents()

    def _pick_fee_mapping_account(self, mapping_key: str) -> None:
        active_import = self._active_marketplace_import()
        if not active_import:
            return

        accounts: tuple[AccountRecord, ...] = self.app_state.get("accounts", ())
        if not accounts:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        current_account_mapping = self._account_mapping(mapping, active_import)
        current_guid = current_account_mapping.fee_accounts.get(mapping_key)

        dialog = AccountPickerDialog(
            accounts,
            selected_guid=current_guid,
            allowed_types={"EXPENSE", "INCOME", "ASSET", "BANK", "CASH", "EQUITY"},
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_guid = dialog.selected_guid()
        if not selected_guid:
            return

        updated_fee_accounts = dict(current_account_mapping.fee_accounts)
        updated_fee_accounts[mapping_key] = selected_guid
        updated_account_mapping = replace(current_account_mapping, fee_accounts=updated_fee_accounts)
        self._save_account_mapping(str(active_import["account_key"]), updated_account_mapping)

    def _save_account_mapping(self, account_key: str, account_mapping: MarketplaceAccountMapping) -> None:
        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        updated_marketplace_accounts = dict(mapping.marketplace_accounts)
        updated_marketplace_accounts[account_key] = account_mapping
        updated_mapping = replace(mapping, marketplace_accounts=updated_marketplace_accounts)
        self.app_state["mapping_config"] = updated_mapping
        book_id = self.app_state.get("book_id")
        if book_id:
            self.app_state["config_store"].save_mapping(book_id, updated_mapping)
        self.app_state["notify_state_changed"]()
