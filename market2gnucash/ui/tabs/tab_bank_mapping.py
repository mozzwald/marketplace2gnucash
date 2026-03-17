from __future__ import annotations

from dataclasses import replace
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.decimal_utils import ZERO
from market2gnucash.core.models import AccountRecord, MappingConfig, PlannedTransactionStatus
from market2gnucash.core.planner import build_plan
from market2gnucash.ui.account_picker import AccountPickerDialog


class MatchOverrideDialog(QDialog):
    def __init__(
        self,
        *,
        bank_description: str,
        bank_amount: str,
        candidates: list[PlannedTransactionStatus],
        selected_ids: tuple[str, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Match Override")
        self.resize(900, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Bank/Card transaction: {bank_description}"))
        layout.addWidget(QLabel(f"Target amount: {bank_amount}"))

        self.candidates_table = QTableWidget()
        self.candidates_table.setColumnCount(7)
        self.candidates_table.setHorizontalHeaderLabels(
            ["Date", "Marketplace", "Market Acct", "Type", "ID", "Amount", "Status"]
        )
        self.candidates_table.setRowCount(len(candidates))
        self.candidates_table.verticalHeader().setVisible(False)
        self.candidates_table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.candidates_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidates_table.setSortingEnabled(False)

        for row_index, status_row in enumerate(candidates):
            txn = status_row.transaction
            row_items = [
                QTableWidgetItem(txn.date.isoformat()),
                QTableWidgetItem(txn.marketplace),
                QTableWidgetItem(txn.marketplace_account_label or ""),
                QTableWidgetItem(txn.txn_kind),
                QTableWidgetItem(txn.txn_id),
                QTableWidgetItem(str(txn.clearing_amount)),
                QTableWidgetItem(status_row.status),
            ]
            row_items[0].setData(Qt.ItemDataRole.UserRole, txn.dedupe_key)
            for col_index, item in enumerate(row_items):
                self.candidates_table.setItem(row_index, col_index, item)
            if txn.dedupe_key in selected_ids:
                self.candidates_table.selectRow(row_index)

        self.candidates_table.setSortingEnabled(True)
        self.candidates_table.resizeColumnsToContents()
        layout.addWidget(self.candidates_table)

        buttons_row = QHBoxLayout()
        save_button = QPushButton("Save Override")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_row.addStretch()
        buttons_row.addWidget(save_button)
        buttons_row.addWidget(cancel_button)
        layout.addLayout(buttons_row)

    def selected_dedupe_keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        seen_rows: set[int] = set()
        for item in self.candidates_table.selectedItems():
            if item.row() in seen_rows:
                continue
            seen_rows.add(item.row())
            dedupe_item = self.candidates_table.item(item.row(), 0)
            if dedupe_item is None:
                continue
            dedupe_key = dedupe_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(dedupe_key, str):
                keys.append(dedupe_key)
        return tuple(keys)


class BankMappingTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        tools_row = QHBoxLayout()
        self.refresh_matches_button = QPushButton("Refresh Bank/Card Preview")
        self.refresh_matches_button.clicked.connect(self._rebuild_plan)
        tools_row.addWidget(self.refresh_matches_button)
        tools_row.addStretch()
        layout.addLayout(tools_row)

        self.txn_table = QTableWidget()
        self.txn_table.setColumnCount(9)
        self.txn_table.setHorizontalHeaderLabels(
            ["Date", "Bank Acct", "Description", "Amount", "Marketplace Txns", "Market Acct", "Account", "Actions", "Rule"]
        )
        self.txn_table.verticalHeader().setVisible(False)
        self.txn_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.txn_table.setSortingEnabled(True)
        layout.addWidget(QLabel("Bank / Card Transactions"))
        layout.addWidget(self.txn_table)

    def refresh_from_state(self) -> None:
        plan = self.app_state.get("plan_result")
        if plan is None:
            self._populate_transaction_table((), (), self.app_state.get("mapping_config", MappingConfig()))
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        self._populate_transaction_table(
            plan.bank_match_results,
            plan.bank_category_results,
            mapping,
            plan.transactions,
        )

    def _populate_transaction_table(
        self,
        match_results: tuple,
        category_results: tuple,
        mapping: MappingConfig,
        planned_transactions: tuple[PlannedTransactionStatus, ...] = (),
    ) -> None:
        self.txn_table.setSortingEnabled(False)
        self.txn_table.setRowCount(len(match_results))
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})
        category_by_key = {result.bank_dedupe_key: result for result in category_results}
        bank_account_by_key: dict[str, str] = {}
        for status_row in planned_transactions:
            txn = status_row.transaction
            if txn.marketplace != "bank":
                continue
            bank_split = next((split for split in txn.splits if split.mapping_key == "bank:account"), None)
            if bank_split is None or not bank_split.account_guid:
                continue
            account = accounts_by_guid.get(bank_split.account_guid)
            if account is None:
                continue
            bank_account_by_key[txn.dedupe_key] = account.full_name.split(":")[-1]

        for row_index, match_result in enumerate(match_results):
            category_result = category_by_key.get(match_result.bank_dedupe_key)
            bank_account_label = bank_account_by_key.get(match_result.bank_dedupe_key, "(unmapped)")
            matched_account_labels = []
            for target in match_result.targets:
                account = accounts_by_guid.get(target.account_guid)
                matched_account_labels.append(account.full_name if account else target.account_guid)
            if matched_account_labels:
                account_label = "\n".join(matched_account_labels)
                mapped = True
            else:
                account_guid = category_result.mapped_account_guid if category_result else None
                account = accounts_by_guid.get(account_guid) if account_guid else None
                account_label = account.full_name if account else "(unmapped)"
                mapped = account is not None
            merchant_rule = category_result.merchant_key if category_result else ""
            marketplace_account_label = "\n".join(match_result.marketplace_account_labels)
            matched_ids = (
                "\n".join(self._format_match_label(value) for value in match_result.matched_transaction_ids)
                if match_result.matched_transaction_ids
                else "(unmatched)"
            )
            matched = match_result.status == "matched"

            row_items = [
                QTableWidgetItem(match_result.bank_date.isoformat()),
                QTableWidgetItem(bank_account_label),
                QTableWidgetItem(match_result.bank_description),
                QTableWidgetItem(str(match_result.bank_amount)),
                QTableWidgetItem(matched_ids),
                QTableWidgetItem(marketplace_account_label),
                QTableWidgetItem(account_label),
                QTableWidgetItem(""),
                QTableWidgetItem(merchant_rule),
            ]
            for col_index, item in enumerate(row_items):
                self.txn_table.setItem(row_index, col_index, item)

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            match_button = QPushButton("Marketplace Match")
            match_button.clicked.connect(
                lambda _checked=False, dedupe_key=match_result.bank_dedupe_key: self._edit_match_override(dedupe_key)
            )
            clear_match_button = QPushButton("Clear Match")
            clear_match_button.clicked.connect(
                lambda _checked=False, dedupe_key=match_result.bank_dedupe_key: self._clear_match_override(dedupe_key)
            )
            actions_layout.addWidget(match_button)
            actions_layout.addWidget(clear_match_button)

            if category_result is not None:
                default_button = QPushButton("Use Default")
                default_button.clicked.connect(
                    lambda _checked=False, key=category_result.merchant_key: self._pick_bank_category_default_account(key)
                )
                account_button = QPushButton("Account")
                account_button.clicked.connect(
                    lambda _checked=False, dedupe_key=category_result.bank_dedupe_key: self._pick_bank_category_transaction_account(dedupe_key)
                )
                clear_account_button = QPushButton("Clear Account")
                clear_account_button.clicked.connect(
                    lambda _checked=False,
                    dedupe_key=category_result.bank_dedupe_key,
                    key=category_result.merchant_key: self._clear_bank_category_mapping(dedupe_key, key)
                )
                actions_layout.addWidget(default_button)
                actions_layout.addWidget(account_button)
                actions_layout.addWidget(clear_account_button)
            actions_layout.addStretch()
            self.txn_table.setCellWidget(row_index, 7, actions_widget)
            self._apply_row_colors(row_index, matched=matched, mapped=mapped, actions_widget=actions_widget)

        self.txn_table.setSortingEnabled(True)
        self.txn_table.resizeColumnsToContents()

    @staticmethod
    def _format_match_label(dedupe_key: str) -> str:
        parts = dedupe_key.split(":")
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
        return dedupe_key

    def _apply_row_colors(
        self,
        row_index: int,
        *,
        matched: bool,
        mapped: bool,
        actions_widget: QWidget,
    ) -> None:
        green = QColor("#93FA8F")
        yellow = QColor("#F8DF63")
        black = QColor("black")

        def style_actions(background: str) -> None:
            actions_widget.setStyleSheet(f"background-color: {background}; color: black;")
            for button in actions_widget.findChildren(QPushButton):
                button.setStyleSheet(f"background-color: {background}; color: black;")

        if matched and mapped:
            for col in range(9):
                item = self.txn_table.item(row_index, col)
                if item is None:
                    continue
                item.setBackground(green)
                item.setForeground(black)
            style_actions("#93FA8F")
            return

        if matched and not mapped:
            for col in range(9):
                item = self.txn_table.item(row_index, col)
                if item is None:
                    continue
                item.setBackground(yellow)
                item.setForeground(black)
            style_actions("#F8DF63")
            return

        if mapped and not matched:
            for col in range(9):
                item = self.txn_table.item(row_index, col)
                if item is None:
                    continue
                item.setBackground(yellow if col == 4 else green)
                item.setForeground(black)
            style_actions("#93FA8F")
            return

        if not matched:
            item = self.txn_table.item(row_index, 4)
            if item is not None:
                item.setBackground(yellow)
                item.setForeground(black)

    def _marketplace_candidate_rows(self) -> list[PlannedTransactionStatus]:
        plan = self.app_state.get("plan_result")
        if plan is None:
            return []
        return [
            row
            for row in plan.transactions
            if row.transaction.marketplace in {"etsy", "ebay"} and row.status != "blocked"
        ]

    def _edit_match_override(self, bank_dedupe_key: str) -> None:
        plan = self.app_state.get("plan_result")
        if plan is None:
            QMessageBox.warning(self, "No Preview", "Generate a preview before editing bank/card matches.")
            return

        match_result = next(
            (result for result in plan.bank_match_results if result.bank_dedupe_key == bank_dedupe_key),
            None,
        )
        if match_result is None:
            QMessageBox.warning(self, "Match Missing", "Could not locate the selected bank/card match row.")
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        current_override = mapping.bank_match_overrides.get(bank_dedupe_key, ())
        dialog = MatchOverrideDialog(
            bank_description=match_result.bank_description,
            bank_amount=str(match_result.bank_amount),
            candidates=self._marketplace_candidate_rows(),
            selected_ids=current_override if current_override else match_result.matched_transaction_ids,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_ids = dialog.selected_dedupe_keys()
        candidate_map = {row.transaction.dedupe_key: row.transaction for row in self._marketplace_candidate_rows()}
        total = sum(
            (candidate_map[key].clearing_amount for key in selected_ids if key in candidate_map),
            start=ZERO,
        )
        if selected_ids and total != match_result.bank_amount:
            QMessageBox.warning(
                self,
                "Amount Mismatch",
                "Selected marketplace transactions must sum exactly to the bank/card amount.",
            )
            return

        updated_overrides = dict(mapping.bank_match_overrides)
        if selected_ids:
            updated_overrides[bank_dedupe_key] = selected_ids
        else:
            updated_overrides.pop(bank_dedupe_key, None)
        self._save_mapping(replace(mapping, bank_match_overrides=updated_overrides), refresh_plan=True)

    def _clear_match_override(self, bank_dedupe_key: str) -> None:
        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        if bank_dedupe_key not in mapping.bank_match_overrides:
            return
        updated_overrides = dict(mapping.bank_match_overrides)
        updated_overrides.pop(bank_dedupe_key, None)
        self._save_mapping(replace(mapping, bank_match_overrides=updated_overrides), refresh_plan=True)

    def _pick_bank_category_default_account(self, merchant_key: str) -> None:
        accounts: tuple[AccountRecord, ...] = self.app_state.get("accounts", ())
        if not accounts:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        current_guid = mapping.bank_merchant_accounts.get(merchant_key)
        dialog = AccountPickerDialog(
            accounts,
            selected_guid=current_guid,
            allowed_types={"EXPENSE", "INCOME", "ASSET", "BANK", "CASH", "CREDIT", "LIABILITY", "EQUITY"},
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_guid = dialog.selected_guid()
        if not selected_guid:
            return

        updated_map = dict(mapping.bank_merchant_accounts)
        updated_map[merchant_key] = selected_guid
        self._save_mapping(replace(mapping, bank_merchant_accounts=updated_map), refresh_plan=True)

    def _pick_bank_category_transaction_account(self, bank_dedupe_key: str) -> None:
        accounts: tuple[AccountRecord, ...] = self.app_state.get("accounts", ())
        if not accounts:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        current_guid = mapping.bank_txn_account_overrides.get(bank_dedupe_key)
        dialog = AccountPickerDialog(
            accounts,
            selected_guid=current_guid,
            allowed_types={"EXPENSE", "INCOME", "ASSET", "BANK", "CASH", "CREDIT", "LIABILITY", "EQUITY"},
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        selected_guid = dialog.selected_guid()
        if not selected_guid:
            return

        updated_map = dict(mapping.bank_txn_account_overrides)
        updated_map[bank_dedupe_key] = selected_guid
        self._save_mapping(replace(mapping, bank_txn_account_overrides=updated_map), refresh_plan=True)

    def _clear_bank_category_mapping(self, bank_dedupe_key: str, merchant_key: str) -> None:
        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        if bank_dedupe_key in mapping.bank_txn_account_overrides:
            updated_txn_map = dict(mapping.bank_txn_account_overrides)
            updated_txn_map.pop(bank_dedupe_key, None)
            self._save_mapping(replace(mapping, bank_txn_account_overrides=updated_txn_map), refresh_plan=True)
            return
        if merchant_key not in mapping.bank_merchant_accounts:
            return
        updated_default_map = dict(mapping.bank_merchant_accounts)
        updated_default_map.pop(merchant_key, None)
        self._save_mapping(replace(mapping, bank_merchant_accounts=updated_default_map), refresh_plan=True)

    def _save_mapping(self, mapping: MappingConfig, refresh_plan: bool = False) -> None:
        self.app_state["mapping_config"] = mapping
        book_id = self.app_state.get("book_id")
        if book_id:
            self.app_state["config_store"].save_mapping(book_id, mapping)
        if refresh_plan and self.app_state.get("plan_result") is not None:
            self._rebuild_plan()
            return
        self.app_state["notify_state_changed"]()

    def _rebuild_plan(self) -> None:
        book_id = self.app_state.get("book_id")
        if not book_id:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        inputs = self.app_state.get("inputs", {})
        use_range = bool(inputs.get("use_date_range"))
        start_date = date.fromisoformat(inputs["start_date"]) if use_range and inputs.get("start_date") else None
        end_date = date.fromisoformat(inputs["end_date"]) if use_range and inputs.get("end_date") else None

        try:
            plan = build_plan(
                book_id=book_id,
                dedupe_store=self.app_state["dedupe_store"],
                mapping=self.app_state["mapping_config"],
                marketplace_imports=inputs.get("marketplace_imports", []),
                bank_imports=inputs.get("bank_imports", []),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Refresh failed", str(exc))
            return

        self.app_state["plan_result"] = plan
        self.app_state["marketplace_mapping_keys"] = dict(plan.marketplace_mapping_keys)
        self.app_state["notify_state_changed"]()
