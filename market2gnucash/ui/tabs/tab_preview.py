from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.models import PlanResult
from market2gnucash.core.planner import build_plan


class PreviewTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        self.plan_button = QPushButton("Plan Import (Dry Run)")
        self.plan_button.clicked.connect(self.plan_import)

        self.status_label = QLabel("No preview generated.")

        self.txn_table = QTableWidget()
        self.txn_table.setColumnCount(7)
        self.txn_table.setHorizontalHeaderLabels(
            ["Date", "Market", "Kind", "ID", "Net", "Status", "Reason"]
        )
        self.txn_table.verticalHeader().setVisible(False)
        self.txn_table.itemSelectionChanged.connect(self._show_selected_transaction_splits)

        self.split_table = QTableWidget()
        self.split_table.setColumnCount(4)
        self.split_table.setHorizontalHeaderLabels(["Account", "Memo", "Amount", "Mapping Key"])
        self.split_table.verticalHeader().setVisible(False)

        self.warning_list = QListWidget()

        layout.addWidget(self.plan_button)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Planned Transactions"))
        layout.addWidget(self.txn_table)
        layout.addWidget(QLabel("Split Breakdown"))
        layout.addWidget(self.split_table)
        layout.addWidget(QLabel("Warnings"))
        layout.addWidget(self.warning_list)

    def refresh_from_state(self) -> None:
        plan: PlanResult | None = self.app_state.get("plan_result")
        if plan is None:
            self.status_label.setText("No preview generated.")
            self.txn_table.setRowCount(0)
            self.split_table.setRowCount(0)
            self.warning_list.clear()
            return

        self._load_plan(plan)

    def plan_import(self) -> None:
        book_id = self.app_state.get("book_id")
        if not book_id:
            QMessageBox.warning(self, "No Book", "Open a book in the Book tab first.")
            return

        inputs = self.app_state.get("inputs", {})
        use_range = bool(inputs.get("use_date_range"))
        start_date = date.fromisoformat(inputs["start_date"]) if use_range and inputs.get("start_date") else None
        end_date = date.fromisoformat(inputs["end_date"]) if use_range and inputs.get("end_date") else None
        bank_imports = inputs.get("bank_imports", [])

        try:
            plan = build_plan(
                book_id=book_id,
                dedupe_store=self.app_state["dedupe_store"],
                mapping=self.app_state["mapping_config"],
                etsy_statement_path=inputs.get("etsy_statement_path"),
                etsy_sold_orders_path=inputs.get("etsy_sold_orders_path"),
                ebay_report_path=inputs.get("ebay_report_path"),
                bank_imports=bank_imports,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Preview failed", str(exc))
            return

        self.app_state["plan_result"] = plan
        self.app_state["etsy_mapping_keys"] = plan.etsy_mapping_keys
        self.app_state["ebay_fee_columns"] = plan.ebay_fee_columns
        self.app_state["notify_state_changed"]()

    def _load_plan(self, plan: PlanResult) -> None:
        total = len(plan.transactions)
        ready = sum(1 for row in plan.transactions if row.status == "ready")
        duplicate = sum(1 for row in plan.transactions if row.status == "duplicate")
        blocked = sum(1 for row in plan.transactions if row.status == "blocked")
        self.status_label.setText(
            f"Planned transactions: {total} | Ready: {ready} | Duplicate: {duplicate} | Blocked: {blocked}"
        )

        self.txn_table.setRowCount(total)
        for row_index, status_row in enumerate(plan.transactions):
            txn = status_row.transaction
            values = [
                txn.date.isoformat(),
                txn.marketplace,
                txn.txn_kind,
                txn.txn_id,
                str(txn.clearing_amount),
                status_row.status,
                status_row.status_reason,
            ]
            for col, value in enumerate(values):
                self.txn_table.setItem(row_index, col, QTableWidgetItem(value))

        self.txn_table.resizeColumnsToContents()

        self.warning_list.clear()
        for warning in plan.warnings:
            self.warning_list.addItem(warning)

        if plan.transactions:
            self.txn_table.selectRow(0)
        else:
            self.split_table.setRowCount(0)

    def _show_selected_transaction_splits(self) -> None:
        selected_items = self.txn_table.selectedItems()
        if not selected_items:
            self.split_table.setRowCount(0)
            return

        row = selected_items[0].row()
        plan: PlanResult | None = self.app_state.get("plan_result")
        if plan is None or row >= len(plan.transactions):
            self.split_table.setRowCount(0)
            return

        transaction = plan.transactions[row].transaction
        accounts_by_guid = self.app_state.get("accounts_by_guid", {})

        self.split_table.setRowCount(len(transaction.splits))
        for idx, split in enumerate(transaction.splits):
            account = accounts_by_guid.get(split.account_guid) if split.account_guid else None
            account_label = account.full_name if account else "(unmapped)"

            self.split_table.setItem(idx, 0, QTableWidgetItem(account_label))
            self.split_table.setItem(idx, 1, QTableWidgetItem(split.memo))
            self.split_table.setItem(idx, 2, QTableWidgetItem(str(split.amount)))
            self.split_table.setItem(idx, 3, QTableWidgetItem(split.mapping_key or ""))

        self.split_table.resizeColumnsToContents()
