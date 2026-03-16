from __future__ import annotations

from dataclasses import replace
from datetime import date

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
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

        self.list_widget = QListWidget()
        for status_row in candidates:
            txn = status_row.transaction
            item = QListWidgetItem(
                f"{txn.date.isoformat()} | {txn.marketplace} | {txn.txn_kind} | {txn.txn_id} | {txn.clearing_amount} | {status_row.status}"
            )
            item.setData(32, txn.dedupe_key)
            item.setSelected(txn.dedupe_key in selected_ids)
            self.list_widget.addItem(item)
        self.list_widget.setSelectionMode(QListWidget.MultiSelection)
        layout.addWidget(self.list_widget)

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
        keys = []
        for item in self.list_widget.selectedItems():
            dedupe_key = item.data(32)
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

        self.match_table = QTableWidget()
        self.match_table.setColumnCount(7)
        self.match_table.setHorizontalHeaderLabels(
            ["Status", "Source", "Date", "Bank/Card Transaction", "Amount", "Matched Marketplace Txns", "Actions"]
        )
        self.match_table.verticalHeader().setVisible(False)
        layout.addWidget(QLabel("Bank / Card Matching"))
        layout.addWidget(self.match_table)

        self.bank_category_table = QTableWidget()
        self.bank_category_table.setColumnCount(7)
        self.bank_category_table.setHorizontalHeaderLabels(
            ["Source", "Merchant Rule", "Date", "Description", "Amount", "Account", "Actions"]
        )
        self.bank_category_table.verticalHeader().setVisible(False)
        layout.addWidget(QLabel("Bank / Card Counterparty Mapping"))
        layout.addWidget(self.bank_category_table)

    def refresh_from_state(self) -> None:
        plan = self.app_state.get("plan_result")
        if plan is None:
            self._populate_match_table(())
            self._populate_bank_category_table((), self.app_state.get("mapping_config", MappingConfig()))
            return

        mapping: MappingConfig = self.app_state.get("mapping_config", MappingConfig())
        self._populate_match_table(plan.bank_match_results)
        self._populate_bank_category_table(plan.bank_category_results, mapping)

    def _populate_match_table(self, match_results: tuple) -> None:
        self.match_table.setRowCount(len(match_results))
        for row_index, match_result in enumerate(match_results):
            matched_ids = "\n".join(match_result.matched_transaction_ids) if match_result.matched_transaction_ids else "(none)"
            self.match_table.setItem(row_index, 0, QTableWidgetItem(match_result.status))
            self.match_table.setItem(row_index, 1, QTableWidgetItem(match_result.match_source))
            self.match_table.setItem(row_index, 2, QTableWidgetItem(match_result.bank_date.isoformat()))
            self.match_table.setItem(row_index, 3, QTableWidgetItem(match_result.bank_description))
            self.match_table.setItem(row_index, 4, QTableWidgetItem(str(match_result.bank_amount)))
            self.match_table.setItem(row_index, 5, QTableWidgetItem(matched_ids))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            override_button = QPushButton("Override")
            override_button.clicked.connect(
                lambda _checked=False, dedupe_key=match_result.bank_dedupe_key: self._edit_match_override(dedupe_key)
            )
            clear_button = QPushButton("Clear")
            clear_button.clicked.connect(
                lambda _checked=False, dedupe_key=match_result.bank_dedupe_key: self._clear_match_override(dedupe_key)
            )
            actions_layout.addWidget(override_button)
            actions_layout.addWidget(clear_button)
            actions_layout.addStretch()
            self.match_table.setCellWidget(row_index, 6, actions_widget)

        self.match_table.resizeColumnsToContents()

    def _populate_bank_category_table(self, category_results: tuple, mapping: MappingConfig) -> None:
        self.bank_category_table.setRowCount(len(category_results))
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})
        for row_index, category_result in enumerate(category_results):
            account = (
                accounts_by_guid.get(category_result.mapped_account_guid)
                if category_result.mapped_account_guid
                else None
            )
            account_label = account.full_name if account else "(unmapped)"

            self.bank_category_table.setItem(row_index, 0, QTableWidgetItem(category_result.mapping_source))
            self.bank_category_table.setItem(row_index, 1, QTableWidgetItem(category_result.merchant_key))
            self.bank_category_table.setItem(row_index, 2, QTableWidgetItem(category_result.txn_date.isoformat()))
            self.bank_category_table.setItem(row_index, 3, QTableWidgetItem(category_result.description))
            self.bank_category_table.setItem(row_index, 4, QTableWidgetItem(str(category_result.amount)))
            self.bank_category_table.setItem(row_index, 5, QTableWidgetItem(account_label))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            default_button = QPushButton("Default")
            default_button.clicked.connect(
                lambda _checked=False, key=category_result.merchant_key: self._pick_bank_category_default_account(key)
            )
            override_button = QPushButton("Override")
            override_button.clicked.connect(
                lambda _checked=False, dedupe_key=category_result.bank_dedupe_key: self._pick_bank_category_transaction_account(dedupe_key)
            )
            clear_button = QPushButton("Clear")
            clear_button.clicked.connect(
                lambda _checked=False,
                dedupe_key=category_result.bank_dedupe_key,
                key=category_result.merchant_key: self._clear_bank_category_mapping(dedupe_key, key)
            )
            actions_layout.addWidget(default_button)
            actions_layout.addWidget(override_button)
            actions_layout.addWidget(clear_button)
            actions_layout.addStretch()
            self.bank_category_table.setCellWidget(row_index, 6, actions_widget)

        self.bank_category_table.resizeColumnsToContents()

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
                etsy_statement_path=inputs.get("etsy_statement_path"),
                etsy_sold_orders_path=inputs.get("etsy_sold_orders_path"),
                ebay_report_path=inputs.get("ebay_report_path"),
                bank_imports=inputs.get("bank_imports", []),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Refresh failed", str(exc))
            return

        self.app_state["plan_result"] = plan
        self.app_state["etsy_mapping_keys"] = plan.etsy_mapping_keys
        self.app_state["ebay_fee_columns"] = plan.ebay_fee_columns
        self.app_state["notify_state_changed"]()
