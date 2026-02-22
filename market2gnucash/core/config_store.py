from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from market2gnucash.core.models import MappingConfig
from market2gnucash.core.paths import config_json_path


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_json_path()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"books": {}}
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if "books" not in data or not isinstance(data["books"], dict):
            return {"books": {}}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        temp_path.replace(self.path)

    def get_book_state(self, book_id: str) -> dict[str, Any]:
        data = self._load()
        books = data.setdefault("books", {})
        state = books.get(book_id)
        if not isinstance(state, dict):
            state = {}
            books[book_id] = state
        return state

    def set_book_state(self, book_id: str, state: dict[str, Any]) -> None:
        data = self._load()
        data.setdefault("books", {})[book_id] = state
        self._save(data)

    def load_mapping(self, book_id: str) -> MappingConfig:
        state = self.get_book_state(book_id)
        mappings = state.get("mapping", {})
        if not isinstance(mappings, dict):
            mappings = {}
        return MappingConfig(
            etsy_clearing_guid=mappings.get("etsy_clearing_guid"),
            etsy_income_guid=mappings.get("etsy_income_guid"),
            etsy_refunds_guid=mappings.get("etsy_refunds_guid"),
            ebay_clearing_guid=mappings.get("ebay_clearing_guid"),
            ebay_income_guid=mappings.get("ebay_income_guid"),
            ebay_refunds_guid=mappings.get("ebay_refunds_guid"),
            etsy_fee_accounts=dict(mappings.get("etsy_fee_accounts", {})),
            ebay_fee_accounts=dict(mappings.get("ebay_fee_accounts", {})),
        )

    def save_mapping(self, book_id: str, mapping: MappingConfig) -> None:
        state = self.get_book_state(book_id)
        state["mapping"] = asdict(mapping)
        self.set_book_state(book_id, state)

    def load_inputs(self, book_id: str) -> dict[str, Any]:
        state = self.get_book_state(book_id)
        inputs = state.get("inputs", {})
        if not isinstance(inputs, dict):
            return {}
        return dict(inputs)

    def save_inputs(self, book_id: str, inputs: dict[str, Any]) -> None:
        state = self.get_book_state(book_id)
        state["inputs"] = dict(inputs)
        self.set_book_state(book_id, state)
