from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from market2gnucash.core.config_store import ConfigStore
from market2gnucash.core.dedupe_store import DedupeStore
from market2gnucash.core.models import MappingConfig
from market2gnucash.ui.tabs.tab_book import BookTab
from market2gnucash.ui.tabs.tab_bank_mapping import BankMappingTab
from market2gnucash.ui.tabs.tab_import import ImportTab
from market2gnucash.ui.tabs.tab_inputs import InputsTab
from market2gnucash.ui.tabs.tab_mapping import MappingTab
from market2gnucash.ui.tabs.tab_preview import PreviewTab
from market2gnucash.ui.tabs.tab_settings import SettingsTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("market2gnucash")
        self.resize(1200, 800)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        self.lock_banner = QLabel()
        self.lock_banner.setStyleSheet(
            "background-color: #8b0000; color: white; font-weight: bold; padding: 8px 12px;"
        )
        self.lock_banner.setVisible(False)
        container_layout.addWidget(self.lock_banner)

        self.tabs = QTabWidget()
        container_layout.addWidget(self.tabs)
        self.setCentralWidget(container)

        self.app_state: dict = {
            "book_path": None,
            "book_id": None,
            "book_lock_files": (),
            "accounts": (),
            "accounts_by_guid": {},
            "mapping_config": MappingConfig(),
            "inputs": {},
            "plan_result": None,
            "marketplace_mapping_keys": {},
            "config_store": ConfigStore(),
            "dedupe_store": DedupeStore(),
            "notify_state_changed": self._notify_tabs,
        }

        self.book_tab = BookTab(self.app_state)
        self.inputs_tab = InputsTab(self.app_state)
        self.mapping_tab = MappingTab(self.app_state)
        self.bank_mapping_tab = BankMappingTab(self.app_state)
        self.preview_tab = PreviewTab(self.app_state)
        self.import_tab = ImportTab(self.app_state)
        self.settings_tab = SettingsTab(self.app_state)

        self.tabs.addTab(self.book_tab, "Book")
        self.tabs.addTab(self.inputs_tab, "Inputs")
        self.tabs.addTab(self.mapping_tab, "Marketplace Mapping")
        self.tabs.addTab(self.bank_mapping_tab, "Bank/Card Mapping")
        self.tabs.addTab(self.preview_tab, "Preview")
        self.tabs.addTab(self.import_tab, "Import")
        self.tabs.addTab(self.settings_tab, "Settings")

        self._notify_tabs()
        self._restore_last_book_if_available()

    def _notify_tabs(self) -> None:
        lock_files = self.app_state.get("book_lock_files", ())
        if lock_files:
            self.lock_banner.setText(
                "LOCKED: This GnuCash book appears to be open. Close GnuCash before importing."
            )
            self.lock_banner.setVisible(True)
        else:
            self.lock_banner.setVisible(False)

        for tab in [
            self.book_tab,
            self.inputs_tab,
            self.mapping_tab,
            self.bank_mapping_tab,
            self.preview_tab,
            self.import_tab,
            self.settings_tab,
        ]:
            refresh = getattr(tab, "refresh_from_state", None)
            if callable(refresh):
                refresh()

    def _restore_last_book_if_available(self) -> None:
        config_store: ConfigStore = self.app_state["config_store"]
        last_book_path = config_store.load_last_book_path()
        if not last_book_path:
            return

        path = Path(last_book_path)
        if not path.exists():
            return

        self.book_tab.load_book(
            path,
            show_errors=False,
            show_locked_warning=False,
            allow_locked=True,
        )
