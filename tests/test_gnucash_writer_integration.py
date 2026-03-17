from __future__ import annotations

import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from market2gnucash.core.gnucash_writer import GnuCashWriter
from market2gnucash.core.models import PlannedSplit, PlannedTransaction


@unittest.skipUnless(
    os.environ.get("RUN_GNUCASH_INTEGRATION") == "1",
    "Set RUN_GNUCASH_INTEGRATION=1 to run GnuCash binding integration tests",
)
class GnuCashWriterIntegrationTests(unittest.TestCase):
    def test_write_single_balanced_transaction(self) -> None:
        try:
            import gnucash
        except Exception as exc:  # pragma: no cover - env-specific
            self.skipTest(f"gnucash module unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            book_path = Path(tmp_dir) / "integration.gnucash"
            guid_asset, guid_income = self._create_book_with_accounts(gnucash, book_path)

            planned = PlannedTransaction(
                dedupe_key="integration:test:1",
                marketplace="test",
                marketplace_account_key=None,
                marketplace_account_label=None,
                txn_kind="sale",
                txn_id="1",
                date=date(2026, 2, 22),
                description="Integration Test Txn",
                external_ref="1",
                clearing_amount=Decimal("10.00"),
                splits=(
                    PlannedSplit(account_guid=guid_asset, amount=Decimal("10.00"), memo="Asset"),
                    PlannedSplit(account_guid=guid_income, amount=Decimal("-10.00"), memo="Income"),
                ),
                source_row_ids=("r1",),
            )

            writer = GnuCashWriter(book_path)
            result = writer.write_transactions([planned])
            self.assertEqual(result.written_keys, ("integration:test:1",))

    def _create_book_with_accounts(self, gnucash_module, book_path: Path) -> tuple[str, str]:
        Session = gnucash_module.Session
        SessionOpenMode = gnucash_module.SessionOpenMode
        Account = gnucash_module.Account
        ACCT_TYPE_ASSET = gnucash_module.ACCT_TYPE_ASSET
        ACCT_TYPE_INCOME = gnucash_module.ACCT_TYPE_INCOME

        session = Session(f"xml://{book_path}", SessionOpenMode.SESSION_NEW_STORE)
        try:
            book = session.book
            root = book.get_root_account()
            commodity = book.get_table().lookup("CURRENCY", "USD")

            asset = Account(book)
            asset.SetName("Assets")
            asset.SetType(ACCT_TYPE_ASSET)
            asset.SetCommodity(commodity)
            asset.SetCommoditySCU(100)
            root.append_child(asset)

            income = Account(book)
            income.SetName("Income")
            income.SetType(ACCT_TYPE_INCOME)
            income.SetCommodity(commodity)
            income.SetCommoditySCU(100)
            root.append_child(income)

            session.save()
            return asset.GetGUID().to_string(), income.GetGUID().to_string()
        finally:
            session.end()
            session.destroy()


if __name__ == "__main__":
    unittest.main()
