from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.models import AccountRecord


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

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Account", "Type"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
