from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from market2gnucash.core.book_io import load_book_info


class BookTab(QWidget):
    def __init__(self, app_state: dict) -> None:
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout(self)

        self.book_path_label = QLabel("Book: (none)")
        self.book_id_label = QLabel("Book ID: (none)")
        self.lock_status_label = QLabel("Lock status: unknown")
        self.bindings_status_label = QLabel("Bindings: checking...")

        layout.addWidget(self.book_path_label)
        layout.addWidget(self.book_id_label)
        layout.addWidget(self.lock_status_label)
        layout.addWidget(self.bindings_status_label)

        self.open_button = QPushButton("Open GnuCash Book...")
        self.open_button.clicked.connect(self.open_book)
        layout.addWidget(self.open_button)

        layout.addStretch()
        self._refresh_bindings_status()

    def refresh_from_state(self) -> None:
        book_path = self.app_state.get("book_path")
        book_id = self.app_state.get("book_id")
        lock_files = self.app_state.get("book_lock_files", ())

        self.book_path_label.setText(f"Book: {book_path or '(none)'}")
        self.book_id_label.setText(f"Book ID: {book_id or '(none)'}")

        if not book_path:
            self.lock_status_label.setText("Lock status: unknown")
        elif lock_files:
            self.lock_status_label.setText(
                "Lock status: LOCKED (close GnuCash first)\n" + "\n".join(lock_files)
            )
        else:
            self.lock_status_label.setText("Lock status: not locked")

        self._refresh_bindings_status()

    def _refresh_bindings_status(self) -> None:
        try:
            import gnucash  # noqa: F401

            self.bindings_status_label.setText("Bindings: available")
        except Exception as exc:
            self.bindings_status_label.setText(f"Bindings: unavailable ({exc})")

    def open_book(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select GnuCash Book",
            "",
            "GnuCash Files (*.gnucash);;All Files (*)",
        )

        if not file_path:
            return

        self.load_book(file_path)

    def load_book(
        self,
        file_path: str | Path,
        *,
        show_errors: bool = True,
        show_locked_warning: bool = True,
        allow_locked: bool = True,
    ) -> bool:
        path = Path(file_path)
        if not path.exists():
            if show_errors:
                QMessageBox.critical(self, "Error", "Selected file does not exist.")
            return False

        try:
            book_info = load_book_info(path)
        except Exception as exc:
            if show_errors:
                QMessageBox.critical(self, "Error", f"Failed to load book metadata:\n{exc}")
            return False

        if book_info.lock_files and not allow_locked:
            return False

        self.app_state["book_path"] = book_info.path
        self.app_state["book_id"] = book_info.book_id
        self.app_state["book_lock_files"] = book_info.lock_files
        self.app_state["accounts"] = book_info.accounts
        self.app_state["accounts_by_guid"] = {account.guid: account for account in book_info.accounts}

        config_store = self.app_state["config_store"]
        self.app_state["mapping_config"] = config_store.load_mapping(book_info.book_id)
        self.app_state["inputs"] = config_store.load_inputs(book_info.book_id)
        self.app_state["plan_result"] = None
        self.app_state["marketplace_mapping_keys"] = {}
        config_store.save_last_book_path(book_info.path)

        self.app_state["notify_state_changed"]()

        if book_info.lock_files and show_locked_warning:
            QMessageBox.warning(
                self,
                "Book Locked",
                "This book appears to be locked/open. Imports are blocked until lock files are gone.",
            )
        return True
