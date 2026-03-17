from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from market2gnucash.core.models import MappingConfig, MarketplaceAccountMapping
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

    def load_app_settings(self) -> dict[str, Any]:
        data = self._load()
        app = data.get("app", {})
        if not isinstance(app, dict):
            return {}
        return dict(app)

    def save_app_settings(self, settings: dict[str, Any]) -> None:
        data = self._load()
        data["app"] = dict(settings)
        self._save(data)

    def load_last_book_path(self) -> str | None:
        settings = self.load_app_settings()
        value = settings.get("last_book_path")
        return value if isinstance(value, str) and value else None

    def save_last_book_path(self, path: str) -> None:
        settings = self.load_app_settings()
        settings["last_book_path"] = path
        self.save_app_settings(settings)

    def book_ids(self) -> tuple[str, ...]:
        books = self._load().get("books", {})
        if not isinstance(books, dict):
            return ()
        return tuple(sorted(key for key in books.keys() if isinstance(key, str)))

    def clear_book_state(self, book_id: str) -> None:
        data = self._load()
        books = data.setdefault("books", {})
        books.pop(book_id, None)
        self._save(data)

    def clear_all(self) -> None:
        self._save({"books": {}, "app": {}})

    def load_mapping(self, book_id: str) -> MappingConfig:
        state = self.get_book_state(book_id)
        mappings = state.get("mapping", {})
        if not isinstance(mappings, dict):
            mappings = {}
        marketplace_accounts: dict[str, MarketplaceAccountMapping] = {}
        raw_marketplace_accounts = mappings.get("marketplace_accounts", {})
        if isinstance(raw_marketplace_accounts, dict):
            for account_key, raw_value in raw_marketplace_accounts.items():
                if not isinstance(account_key, str) or not isinstance(raw_value, dict):
                    continue
                fee_accounts = raw_value.get("fee_accounts", {})
                marketplace_accounts[account_key] = MarketplaceAccountMapping(
                    marketplace=raw_value.get("marketplace") if isinstance(raw_value.get("marketplace"), str) else "",
                    account_label=raw_value.get("account_label") if isinstance(raw_value.get("account_label"), str) else account_key,
                    clearing_guid=raw_value.get("clearing_guid") if isinstance(raw_value.get("clearing_guid"), str) else None,
                    income_guid=raw_value.get("income_guid") if isinstance(raw_value.get("income_guid"), str) else None,
                    refunds_guid=raw_value.get("refunds_guid") if isinstance(raw_value.get("refunds_guid"), str) else None,
                    fee_accounts=dict(fee_accounts) if isinstance(fee_accounts, dict) else {},
                )
        return MappingConfig(
            marketplace_accounts=marketplace_accounts,
            bank_match_overrides={
                key: tuple(value)
                for key, value in dict(mappings.get("bank_match_overrides", {})).items()
                if isinstance(value, (list, tuple))
            },
            bank_merchant_accounts=dict(mappings.get("bank_merchant_accounts", {})),
            bank_txn_account_overrides=dict(mappings.get("bank_txn_account_overrides", {})),
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
