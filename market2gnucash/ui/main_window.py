from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from market2gnucash.core.config_store import ConfigStore
from market2gnucash.core.dedupe_store import DedupeStore
from market2gnucash.core.models import MappingConfig
from market2gnucash.ui.tabs.tab_book import BookTab
from market2gnucash.ui.tabs.tab_import import ImportTab
from market2gnucash.ui.tabs.tab_inputs import InputsTab
from market2gnucash.ui.tabs.tab_mapping import MappingTab
from market2gnucash.ui.tabs.tab_preview import PreviewTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("market2gnucash")
        self.resize(1200, 800)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.app_state: dict = {
            "book_path": None,
            "book_id": None,
            "book_lock_files": (),
            "accounts": (),
            "accounts_by_guid": {},
            "mapping_config": MappingConfig(),
            "inputs": {},
            "plan_result": None,
            "config_store": ConfigStore(),
            "dedupe_store": DedupeStore(),
            "notify_state_changed": self._notify_tabs,
        }

        self.book_tab = BookTab(self.app_state)
        self.inputs_tab = InputsTab(self.app_state)
        self.mapping_tab = MappingTab(self.app_state)
        self.preview_tab = PreviewTab(self.app_state)
        self.import_tab = ImportTab(self.app_state)

        self.tabs.addTab(self.book_tab, "Book")
        self.tabs.addTab(self.inputs_tab, "Inputs")
        self.tabs.addTab(self.mapping_tab, "Mapping")
        self.tabs.addTab(self.preview_tab, "Preview")
        self.tabs.addTab(self.import_tab, "Import")

        self._notify_tabs()

    def _notify_tabs(self) -> None:
        for tab in [self.book_tab, self.inputs_tab, self.mapping_tab, self.preview_tab, self.import_tab]:
            refresh = getattr(tab, "refresh_from_state", None)
            if callable(refresh):
                refresh()
