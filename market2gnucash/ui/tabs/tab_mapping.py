from __future__ import annotations

from dataclasses import replace
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.models import AccountRecord, MappingConfig
from market2gnucash.core.parsers import parse_ebay_report, parse_etsy_statement
from market2gnucash.core.rules import etsy_mapping_key


class AccountPickerDialog(QDialog):
    def __init__(
        self,
        accounts: tuple[AccountRecord, ...],
        *,
        selected_guid: str | None,
        allowed_types: set[str] | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select account")
        self.resize(700, 500)

        self._accounts_by_guid = {account.guid: account for account in accounts}

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Account", "Type"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.tree)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        nodes_by_path: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for account in sorted(accounts, key=lambda value: value.full_name):
            if account.account_type == "ROOT" or not account.full_name:
                continue

            parts = account.full_name.split(":")
            parent_item = self.tree.invisibleRootItem()
            for depth in range(len(parts)):
                path = tuple(parts[: depth + 1])
                node = nodes_by_path.get(path)
                if node is None:
                    node = QTreeWidgetItem([parts[depth], ""])
                    parent_item.addChild(node)
                    nodes_by_path[path] = node
                parent_item = node

            parent_item.setText(1, account.account_type)
            parent_item.setData(0, Qt.UserRole, account.guid)

            if allowed_types and account.account_type not in allowed_types:
                parent_item.setDisabled(True)

            if selected_guid and account.guid == selected_guid:
                self.tree.setCurrentItem(parent_item)

        self.tree.expandToDepth(2)

    def selected_guid(self) -> str | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        guid = item.data(0, Qt.UserRole)
        if isinstance(guid, str) and guid:
            return guid
        return None


class MappingTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        account_group = QGroupBox("Marketplace Core Accounts")
        account_layout = QGridLayout(account_group)

        self.account_labels: dict[str, QLabel] = {}

        account_rows = [
            ("etsy_clearing_guid", "Etsy Clearing", {"ASSET", "BANK", "CASH"}),
            ("etsy_income_guid", "Etsy Sales Income", {"INCOME"}),
            ("etsy_refunds_guid", "Etsy Refunds Expense", {"EXPENSE"}),
            ("ebay_clearing_guid", "eBay Clearing", {"ASSET", "BANK", "CASH"}),
            ("ebay_income_guid", "eBay Sales Income", {"INCOME"}),
            ("ebay_refunds_guid", "eBay Refunds Expense", {"EXPENSE"}),
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

        layout.addWidget(account_group)

        tools_row = QHBoxLayout()
        self.scan_button = QPushButton("Scan Inputs for Mapping Keys")
        self.scan_button.clicked.connect(self._scan_inputs)
        tools_row.addWidget(self.scan_button)
        tools_row.addStretch()
        layout.addLayout(tools_row)

        self.etsy_table = QTableWidget()
        self.etsy_table.setColumnCount(3)
        self.etsy_table.setHorizontalHeaderLabels(["Etsy Key", "Account", "Action"])
        self.etsy_table.verticalHeader().setVisible(False)
        layout.addWidget(QLabel("Etsy Fee Mapping"))
        layout.addWidget(self.etsy_table)

        self.ebay_table = QTableWidget()
        self.ebay_table.setColumnCount(3)
        self.ebay_table.setHorizontalHeaderLabels(["eBay Fee Column", "Account", "Action"])
        self.ebay_table.verticalHeader().setVisible(False)
        layout.addWidget(QLabel("eBay Fee Column Mapping"))
        layout.addWidget(self.ebay_table)

    def refresh_from_state(self) -> None:
        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})

        for field_name, label in self.account_labels.items():
            guid = getattr(mapping, field_name)
            account = accounts_by_guid.get(guid) if guid else None
            if account:
                label.setText(f"{account.full_name} ({account.guid})")
            else:
                label.setText("(not selected)")

        etsy_keys = set(self.app_state.get("etsy_mapping_keys", ()))
        ebay_columns = set(self.app_state.get("ebay_fee_columns", ()))

        plan = self.app_state.get("plan_result")
        if plan is not None:
            etsy_keys.update(plan.etsy_mapping_keys)
            ebay_columns.update(plan.ebay_fee_columns)

        self._populate_etsy_table(sorted(etsy_keys), mapping)
        self._populate_ebay_table(sorted(ebay_columns), mapping)

    def _scan_inputs(self) -> None:
        inputs = self.app_state.get("inputs", {})
        use_range = bool(inputs.get("use_date_range"))
        start_date = date.fromisoformat(inputs["start_date"]) if use_range and inputs.get("start_date") else None
        end_date = date.fromisoformat(inputs["end_date"]) if use_range and inputs.get("end_date") else None

        etsy_keys: set[str] = set(self.app_state.get("etsy_mapping_keys", ()))
        ebay_columns: set[str] = set(self.app_state.get("ebay_fee_columns", ()))

        try:
            statement_path = inputs.get("etsy_statement_path")
            if statement_path:
                statement_rows = parse_etsy_statement(statement_path, start_date, end_date)
                for row in statement_rows:
                    if row.row_type != "Fee":
                        continue
                    etsy_keys.add(etsy_mapping_key(row))
                    if row.title.startswith("Transaction fee:") and row.title != "Transaction fee: Shipping":
                        etsy_keys.add("etsy:Fee:Transaction fee:*")

            ebay_report_path = inputs.get("ebay_report_path")
            if ebay_report_path:
                ebay_data = parse_ebay_report(ebay_report_path, start_date, end_date)
                ebay_columns.update(ebay_data.fee_columns)
        except Exception as exc:
            QMessageBox.critical(self, "Scan failed", str(exc))
            return

        self.app_state["etsy_mapping_keys"] = tuple(sorted(etsy_keys))
        self.app_state["ebay_fee_columns"] = tuple(sorted(ebay_columns))
        self.app_state["notify_state_changed"]()

    def _pick_core_account(self, field_name: str, allowed_types: set[str]) -> None:
        accounts: tuple[AccountRecord, ...] = self.app_state.get("accounts", ())
        if not accounts:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        current_guid = getattr(mapping, field_name)

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

        updated = replace(mapping, **{field_name: selected_guid})
        self._save_mapping(updated)

    def _populate_etsy_table(self, keys: list[str], mapping: MappingConfig) -> None:
        self.etsy_table.setRowCount(len(keys))
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})

        for row_index, key in enumerate(keys):
            self.etsy_table.setItem(row_index, 0, QTableWidgetItem(key))

            guid = mapping.etsy_fee_accounts.get(key)
            account = accounts_by_guid.get(guid) if guid else None
            account_label = account.full_name if account else "(unmapped)"
            self.etsy_table.setItem(row_index, 1, QTableWidgetItem(account_label))

            button = QPushButton("Select")
            button.clicked.connect(lambda _checked=False, k=key: self._pick_etsy_mapping_account(k))
            self.etsy_table.setCellWidget(row_index, 2, button)

        self.etsy_table.resizeColumnsToContents()

    def _populate_ebay_table(self, columns: list[str], mapping: MappingConfig) -> None:
        self.ebay_table.setRowCount(len(columns))
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})

        for row_index, column_name in enumerate(columns):
            mapping_key = f"ebay:fee_col:{column_name}"
            self.ebay_table.setItem(row_index, 0, QTableWidgetItem(column_name))

            guid = mapping.ebay_fee_accounts.get(mapping_key)
            account = accounts_by_guid.get(guid) if guid else None
            account_label = account.full_name if account else "(unmapped)"
            self.ebay_table.setItem(row_index, 1, QTableWidgetItem(account_label))

            button = QPushButton("Select")
            button.clicked.connect(
                lambda _checked=False, c=column_name: self._pick_ebay_mapping_account(c)
            )
            self.ebay_table.setCellWidget(row_index, 2, button)

        self.ebay_table.resizeColumnsToContents()

    def _pick_etsy_mapping_account(self, key: str) -> None:
        self._pick_fee_mapping_account(
            mapping_key=key,
            marketplace="etsy",
        )

    def _pick_ebay_mapping_account(self, column_name: str) -> None:
        self._pick_fee_mapping_account(
            mapping_key=f"ebay:fee_col:{column_name}",
            marketplace="ebay",
        )

    def _pick_fee_mapping_account(self, mapping_key: str, marketplace: str) -> None:
        accounts: tuple[AccountRecord, ...] = self.app_state.get("accounts", ())
        if not accounts:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        if marketplace == "etsy":
            current_guid = mapping.etsy_fee_accounts.get(mapping_key)
        else:
            current_guid = mapping.ebay_fee_accounts.get(mapping_key)

        dialog = AccountPickerDialog(
            accounts,
            selected_guid=current_guid,
            allowed_types={"EXPENSE", "INCOME", "ASSET", "BANK", "CASH"},
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_guid = dialog.selected_guid()
        if not selected_guid:
            return

        if marketplace == "etsy":
            updated_map = dict(mapping.etsy_fee_accounts)
            updated_map[mapping_key] = selected_guid
            updated = replace(mapping, etsy_fee_accounts=updated_map)
        else:
            updated_map = dict(mapping.ebay_fee_accounts)
            updated_map[mapping_key] = selected_guid
            updated = replace(mapping, ebay_fee_accounts=updated_map)

        self._save_mapping(updated)

    def _save_mapping(self, mapping: MappingConfig) -> None:
        self.app_state["mapping_config"] = mapping
        book_id = self.app_state.get("book_id")
        if book_id:
            self.app_state["config_store"].save_mapping(book_id, mapping)
        self.app_state["notify_state_changed"]()
