from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from market2gnucash.core.models import (
    BankImportSpec,
    MappingConfig,
    MarketplaceAccountMapping,
    PlannedSplit,
    PlannedTransaction,
)
from market2gnucash.core.parsers import parse_bank_statement_file, parse_ebay_report, parse_etsy_inputs
from market2gnucash.core.rules import (
    bank_merchant_key,
    build_bank_transactions,
    build_ebay_transactions,
    build_ebay_payout_match_candidates,
    build_etsy_deposit_match_candidates,
    build_etsy_payment_match_candidates,
    build_etsy_transactions,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "sample_imports"
RS_SAMPLES = SAMPLES / "RS"


class RuleEngineTests(unittest.TestCase):
    def _etsy_mapping(self, account_key: str = "etsy:shop-a", account_label: str = "Etsy Shop A") -> MappingConfig:
        etsy_fee_accounts = {
            "etsy:Fee:Listing fee": "guid-exp-listing",
            "etsy:Fee:Transaction fee: Shipping": "guid-exp-fee",
            "etsy:Fee:Transaction fee:*": "guid-exp-fee",
            "etsy:Fee:Processing fee": "guid-exp-fee",
            "etsy:Fee:Credit for transaction fee on shipping": "guid-exp-fee",
            "etsy:Fee:Credit for processing fee": "guid-exp-fee",
        }
        return MappingConfig(
            marketplace_accounts={
                account_key: MarketplaceAccountMapping(
                    marketplace="etsy",
                    account_label=account_label,
                    clearing_guid="guid-asset-etsy",
                    income_guid="guid-income-etsy",
                    refunds_guid="guid-exp-refunds",
                    fee_accounts=etsy_fee_accounts,
                )
            },
        )

    def _ebay_mapping(self, fee_columns: tuple[str, ...], account_key: str = "ebay:main", account_label: str = "eBay Main") -> MappingConfig:
        ebay_fee_accounts = {f"ebay:fee_col:{col}": "guid-exp-ebay-fees" for col in fee_columns}
        return MappingConfig(
            marketplace_accounts={
                account_key: MarketplaceAccountMapping(
                    marketplace="ebay",
                    account_label=account_label,
                    clearing_guid="guid-asset-ebay",
                    income_guid="guid-income-ebay",
                    refunds_guid="guid-exp-refunds-ebay",
                    fee_accounts=ebay_fee_accounts,
                )
            },
        )

    def _bank_mapping(self, account_key: str = "etsy:shop-a", account_label: str = "Etsy Shop A") -> MappingConfig:
        return self._etsy_mapping(account_key=account_key, account_label=account_label)

    def test_etsy_rules_one_sale_per_order_and_listing_per_row(self) -> None:
        etsy_data = parse_etsy_inputs(
            RS_SAMPLES / "etsy_statement_2026_1.csv",
            RS_SAMPLES / "EtsySoldOrders2026-1.csv",
        )

        transactions, warnings, _keys = build_etsy_transactions(
            etsy_data,
            self._etsy_mapping(),
            account_key="etsy:shop-a",
            account_label="Etsy Shop A",
        )

        sales = [txn for txn in transactions if txn.txn_kind == "sale"]
        refunds = [txn for txn in transactions if txn.txn_kind == "refund"]
        listings = [txn for txn in transactions if txn.txn_kind == "listing_fee"]

        self.assertEqual(len({txn.txn_id for txn in sales}), len(sales))

        listing_rows = [
            row for row in etsy_data.statement_rows if row.row_type == "Fee" and row.title == "Listing fee"
        ]
        self.assertEqual(len(listings), len(listing_rows))

        refund_rows = [row for row in etsy_data.statement_rows if row.row_type == "Refund"]
        self.assertEqual(len(refunds), len(refund_rows))

        for txn in transactions:
            self.assertEqual(sum(split.amount for split in txn.splits), Decimal("0"))

        self.assertTrue(all("UNBALANCED" not in warning for warning in warnings))

    def test_ebay_rules_one_sale_per_order(self) -> None:
        ebay_data = parse_ebay_report(SAMPLES / "eBay-Transaction_report_20260101_20260131.csv")
        mapping = self._ebay_mapping(ebay_data.fee_columns)

        transactions, _warnings, _columns = build_ebay_transactions(
            ebay_data,
            mapping,
            account_key="ebay:main",
            account_label="eBay Main",
        )

        sales = [txn for txn in transactions if txn.txn_kind == "sale"]
        refunds = [txn for txn in transactions if txn.txn_kind == "refund"]

        self.assertEqual(len({txn.txn_id for txn in sales}), len(sales))
        self.assertGreaterEqual(len(refunds), 1)

        row_counts = Counter(row.order_number for row in ebay_data.report_rows if row.row_type == "Order")
        for txn in sales:
            self.assertIn(txn.txn_id, row_counts)

        for txn in transactions:
            self.assertEqual(sum(split.amount for split in txn.splits), Decimal("0"))

        for refund in refunds:
            income_memos = [split.memo for split in refund.splits if "sales income" in split.memo.lower()]
            self.assertEqual(income_memos, [])

    def test_marketplace_dedupe_keys_do_not_collide_across_accounts(self) -> None:
        etsy_data = parse_etsy_inputs(
            RS_SAMPLES / "etsy_statement_2026_1.csv",
            RS_SAMPLES / "EtsySoldOrders2026-1.csv",
        )
        mapping = MappingConfig(
            marketplace_accounts={
                **self._etsy_mapping("etsy:shop-a", "Shop A").marketplace_accounts,
                **self._etsy_mapping("etsy:shop-b", "Shop B").marketplace_accounts,
            }
        )

        shop_a_txns, _warnings_a, _keys_a = build_etsy_transactions(
            etsy_data,
            mapping,
            account_key="etsy:shop-a",
            account_label="Shop A",
        )
        shop_b_txns, _warnings_b, _keys_b = build_etsy_transactions(
            etsy_data,
            mapping,
            account_key="etsy:shop-b",
            account_label="Shop B",
        )

        self.assertTrue(shop_a_txns)
        self.assertEqual(len({txn.dedupe_key for txn in shop_a_txns + shop_b_txns}), len(shop_a_txns) + len(shop_b_txns))
        self.assertTrue(all(txn.marketplace_account_key == "etsy:shop-a" for txn in shop_a_txns))
        self.assertTrue(all(txn.marketplace_account_key == "etsy:shop-b" for txn in shop_b_txns))

    def test_bank_rules_one_transaction_per_statement_row(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Amount,Reference,Currency",
                "03/01/2026,Coffee,-4.50,A1,USD",
                "03/02/2026,Refund,10.00,A2,USD",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "checking.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid="guid-bank-account", statement_paths=(str(path),))
        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            self._bank_mapping("unused"),
            (),
        )

        self.assertEqual(len(transactions), 2)
        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(match_results), 2)
        self.assertEqual(len(category_results), 2)
        self.assertTrue(all(result.mapping_source == "unmapped" for result in category_results))
        self.assertEqual({txn.txn_kind for txn in transactions}, {"statement"})

        for txn in transactions:
            self.assertEqual(sum(split.amount for split in txn.splits), Decimal("0"))
            self.assertEqual(len(txn.splits), 2)
            self.assertTrue(any(w.startswith("UNMAPPED") for w in txn.warnings))

    def test_bank_rules_block_when_mapping_missing(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Amount",
                "03/01/2026,Coffee,-4.50",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "card.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid=None, statement_paths=(str(path),))
        transactions, _warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            MappingConfig(),
            (),
        )
        self.assertEqual(len(transactions), 1)
        self.assertEqual(len(match_results), 1)
        self.assertEqual(len(category_results), 1)
        self.assertEqual(category_results[0].mapping_source, "unmapped")
        self.assertTrue(any(w.startswith("MISSING_ACCOUNT") for w in transactions[0].warnings))

    def test_bank_rules_match_marketplace_clearing_transactions(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Amount,Reference,Currency",
                "03/03/2026,Etsy Deposit,80.00,B1,USD",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "checking.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid="guid-bank-account", statement_paths=(str(path),))
        marketplace_transactions = (
            PlannedTransaction(
                dedupe_key="etsy:sale:etsy:shop-a:1",
                marketplace="etsy",
                marketplace_account_key="etsy:shop-a",
                marketplace_account_label="Etsy Shop A",
                txn_kind="sale",
                txn_id="1",
                date=date(2026, 3, 1),
                description="Etsy Sale 1",
                external_ref="1",
                clearing_amount=Decimal("50.00"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-etsy", amount=Decimal("50.00"), memo="clearing"),
                    PlannedSplit(account_guid="guid-income-etsy", amount=Decimal("-50.00"), memo="income"),
                ),
                source_row_ids=("r1",),
            ),
            PlannedTransaction(
                dedupe_key="etsy:sale:etsy:shop-a:2",
                marketplace="etsy",
                marketplace_account_key="etsy:shop-a",
                marketplace_account_label="Etsy Shop A",
                txn_kind="sale",
                txn_id="2",
                date=date(2026, 3, 2),
                description="Etsy Sale 2",
                external_ref="2",
                clearing_amount=Decimal("30.00"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-etsy", amount=Decimal("30.00"), memo="clearing"),
                    PlannedSplit(account_guid="guid-income-etsy", amount=Decimal("-30.00"), memo="income"),
                ),
                source_row_ids=("r2",),
            ),
        )

        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            self._bank_mapping("unused"),
            marketplace_transactions,
        )

        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(len(match_results), 1)
        self.assertEqual(len(category_results), 0)
        self.assertEqual(match_results[0].status, "matched")
        self.assertEqual(
            match_results[0].matched_transaction_ids,
            ("etsy:sale:etsy:shop-a:1", "etsy:sale:etsy:shop-a:2"),
        )
        self.assertEqual(sum(split.amount for split in transactions[0].splits), Decimal("0"))
        self.assertEqual(len(transactions[0].splits), 2)
        self.assertTrue(all(split.mapping_key != "bank:suspense" for split in transactions[0].splits))

    def test_bank_rules_match_etsy_deposit_candidates_before_subset_matching(self) -> None:
        etsy_data = parse_etsy_inputs(
            RS_SAMPLES / "etsy_statement_2026_2.csv",
            RS_SAMPLES / "EtsySoldOrders2026-2.csv",
        )
        deposit_candidates = build_etsy_deposit_match_candidates(
            etsy_data,
            self._etsy_mapping(),
            account_key="etsy:shop-a",
            account_label="Etsy Shop A",
        )
        target = next(candidate for candidate in deposit_candidates if candidate.clearing_amount == Decimal("118.37"))

        csv_text = "\n".join(
            [
                "Date,Description,Amount,Reference,Currency",
                f"{target.date.month:02d}/{target.date.day:02d}/{target.date.year},ETSY INC Payout,118.37,BDEP1,USD",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "checking.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid="guid-bank-account", statement_paths=(str(path),))
        marketplace_transactions = (
            PlannedTransaction(
                dedupe_key="etsy:sale:etsy:shop-a:subset-a",
                marketplace="etsy",
                marketplace_account_key="etsy:shop-a",
                marketplace_account_label="Etsy Shop A",
                txn_kind="sale",
                txn_id="subset-a",
                date=target.date,
                description="Subset A",
                external_ref="subset-a",
                clearing_amount=Decimal("100.00"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-etsy", amount=Decimal("100.00"), memo="clearing"),
                    PlannedSplit(account_guid="guid-income-etsy", amount=Decimal("-100.00"), memo="income"),
                ),
                source_row_ids=("sa",),
            ),
            PlannedTransaction(
                dedupe_key="etsy:sale:etsy:shop-a:subset-b",
                marketplace="etsy",
                marketplace_account_key="etsy:shop-a",
                marketplace_account_label="Etsy Shop A",
                txn_kind="sale",
                txn_id="subset-b",
                date=target.date,
                description="Subset B",
                external_ref="subset-b",
                clearing_amount=Decimal("18.37"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-etsy", amount=Decimal("18.37"), memo="clearing"),
                    PlannedSplit(account_guid="guid-income-etsy", amount=Decimal("-18.37"), memo="income"),
                ),
                source_row_ids=("sb",),
            ),
        )

        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            self._bank_mapping("unused"),
            marketplace_transactions,
            marketplace_payout_candidates=deposit_candidates,
        )

        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(len(category_results), 0)
        self.assertEqual(match_results[0].status, "matched")
        self.assertEqual(match_results[0].match_source, "deposit")
        self.assertEqual(match_results[0].matched_transaction_ids, (target.dedupe_key,))

    def test_bank_rules_match_sample_ebay_payout_candidate(self) -> None:
        statement = parse_bank_statement_file(SAMPLES / "hometown.csv")
        bank_row = next(row for row in statement.rows if row.date == date(2026, 1, 7) and row.amount == Decimal("4.78"))

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "checking.csv"
            path.write_text(
                "\n".join(
                    [
                        "Date,Description,Amount",
                        f"{bank_row.date.month:02d}/{bank_row.date.day:02d}/{bank_row.date.year},{bank_row.description},{bank_row.amount}",
                    ]
                ),
                encoding="utf-8",
            )
            mini_statement = parse_bank_statement_file(path)

        ebay_data = parse_ebay_report(SAMPLES / "eBay-Transaction_report_20260101_20260131.csv")
        payout_candidates = build_ebay_payout_match_candidates(
            ebay_data,
            self._ebay_mapping(ebay_data.fee_columns),
            account_key="ebay:main",
            account_label="eBay Main",
        )
        self.assertTrue(any(candidate.clearing_amount == Decimal("4.78") for candidate in payout_candidates))

        bank_import = BankImportSpec(account_guid="guid-bank-account", statement_paths=(str(path),))
        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (mini_statement,)),),
            self._ebay_mapping(ebay_data.fee_columns),
            (),
            marketplace_payout_candidates=payout_candidates,
        )

        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(len(category_results), 0)
        self.assertEqual(match_results[0].status, "matched")
        self.assertEqual(match_results[0].match_source, "payout")
        self.assertTrue(match_results[0].matched_transaction_ids[0].startswith("ebay:payout:"))

    def test_bank_rules_match_etsy_payment_candidates(self) -> None:
        etsy_data = parse_etsy_inputs(
            RS_SAMPLES / "etsy_statement_2026_1.csv",
            RS_SAMPLES / "EtsySoldOrders2026-1.csv",
        )
        payment_candidates = build_etsy_payment_match_candidates(
            etsy_data,
            self._etsy_mapping(),
            account_key="etsy:shop-a",
            account_label="Etsy Shop A",
        )
        target = next(candidate for candidate in payment_candidates if candidate.clearing_amount == Decimal("-1.00"))

        csv_text = "\n".join(
            [
                "Date,Description,Amount,Reference,Currency",
                f"{target.date.month:02d}/{target.date.day:02d}/{target.date.year},ETSY CARD PAYMENT,-1.00,EPAY1,USD",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "card.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid="guid-card-account", statement_paths=(str(path),))
        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            self._etsy_mapping(),
            (),
            marketplace_payout_candidates=payment_candidates,
        )

        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(len(category_results), 0)
        self.assertEqual(match_results[0].status, "matched")
        self.assertEqual(match_results[0].match_source, "payment")
        self.assertEqual(match_results[0].matched_transaction_ids, (target.dedupe_key,))

    def test_bank_rules_mark_ambiguous_when_multiple_accounts_have_same_payout(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Amount,Reference,Currency",
                "03/20/2026,Marketplace Deposit,80.00,B3,USD",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "checking.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid="guid-bank-account", statement_paths=(str(path),))
        payout_candidates = (
            PlannedTransaction(
                dedupe_key="etsy:deposit:etsy:shop-a:dep1",
                marketplace="etsy",
                marketplace_account_key="etsy:shop-a",
                marketplace_account_label="Etsy Shop A",
                txn_kind="deposit_match",
                txn_id="dep1",
                date=date(2026, 3, 20),
                description="Etsy deposit",
                external_ref="dep1",
                clearing_amount=Decimal("80.00"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-etsy", amount=Decimal("80.00"), memo="clearing"),
                    PlannedSplit(account_guid=None, amount=Decimal("-80.00"), memo="offset"),
                ),
                source_row_ids=("dep1",),
            ),
            PlannedTransaction(
                dedupe_key="ebay:payout:ebay:main:pay1",
                marketplace="ebay",
                marketplace_account_key="ebay:main",
                marketplace_account_label="eBay Main",
                txn_kind="payout_match",
                txn_id="pay1",
                date=date(2026, 3, 20),
                description="eBay payout",
                external_ref="pay1",
                clearing_amount=Decimal("80.00"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-ebay", amount=Decimal("80.00"), memo="clearing"),
                    PlannedSplit(account_guid=None, amount=Decimal("-80.00"), memo="offset"),
                ),
                source_row_ids=("pay1",),
            ),
        )

        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            MappingConfig(),
            (),
            marketplace_payout_candidates=payout_candidates,
        )

        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(len(category_results), 0)
        self.assertEqual(match_results[0].status, "ambiguous")
        self.assertEqual(set(match_results[0].marketplace_account_labels), {"Etsy Shop A", "eBay Main"})

    def test_bank_rules_manual_override_is_used(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Amount,Reference,Currency",
                "03/20/2026,Etsy Deposit,80.00,B2,USD",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "checking.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid="guid-bank-account", statement_paths=(str(path),))
        marketplace_transactions = (
            PlannedTransaction(
                dedupe_key="etsy:sale:etsy:shop-a:10",
                marketplace="etsy",
                marketplace_account_key="etsy:shop-a",
                marketplace_account_label="Etsy Shop A",
                txn_kind="sale",
                txn_id="10",
                date=date(2026, 3, 1),
                description="Etsy Sale 10",
                external_ref="10",
                clearing_amount=Decimal("50.00"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-etsy", amount=Decimal("50.00"), memo="clearing"),
                    PlannedSplit(account_guid="guid-income-etsy", amount=Decimal("-50.00"), memo="income"),
                ),
                source_row_ids=("r10",),
            ),
            PlannedTransaction(
                dedupe_key="etsy:sale:etsy:shop-a:20",
                marketplace="etsy",
                marketplace_account_key="etsy:shop-a",
                marketplace_account_label="Etsy Shop A",
                txn_kind="sale",
                txn_id="20",
                date=date(2026, 3, 2),
                description="Etsy Sale 20",
                external_ref="20",
                clearing_amount=Decimal("30.00"),
                splits=(
                    PlannedSplit(account_guid="guid-asset-etsy", amount=Decimal("30.00"), memo="clearing"),
                    PlannedSplit(account_guid="guid-income-etsy", amount=Decimal("-30.00"), memo="income"),
                ),
                source_row_ids=("r20",),
            ),
        )

        bank_dedupe_key = "bank:guid-bank-account:B2"
        mapping = replace(
            self._etsy_mapping(),
            bank_match_overrides={
                bank_dedupe_key: ("etsy:sale:etsy:shop-a:10", "etsy:sale:etsy:shop-a:20")
            },
        )
        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            mapping,
            marketplace_transactions,
        )

        self.assertEqual(len(warnings), 0)
        self.assertEqual(match_results[0].status, "matched")
        self.assertEqual(match_results[0].match_source, "manual")
        self.assertEqual(len(category_results), 0)
        self.assertTrue(all(split.mapping_key != "bank:suspense" for split in transactions[0].splits))

    def test_bank_rules_apply_persistent_fuzzy_merchant_mapping(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Amount",
                "03/01/2026,Starbucks #1234,-5.00",
                "03/02/2026,Starbucks #5678,-6.25",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "card.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        merchant_key = bank_merchant_key("Starbucks #1234")
        self.assertEqual(merchant_key, bank_merchant_key("Starbucks #5678"))

        bank_import = BankImportSpec(account_guid="guid-card", statement_paths=(str(path),))
        mapping = MappingConfig(
            bank_merchant_accounts={merchant_key: "guid-exp-coffee"},
        )
        transactions, warnings, match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            mapping,
            (),
        )

        self.assertEqual(len(warnings), 0)
        self.assertTrue(all(result.status == "unmatched" for result in match_results))
        self.assertEqual(len(category_results), 2)
        self.assertTrue(all(result.merchant_key == merchant_key for result in category_results))
        self.assertTrue(all(result.mapped_account_guid == "guid-exp-coffee" for result in category_results))
        self.assertTrue(all(result.mapping_source == "merchant" for result in category_results))
        for txn in transactions:
            self.assertTrue(any(split.account_guid == "guid-exp-coffee" for split in txn.splits))
            self.assertTrue(all(split.mapping_key != "bank:suspense" for split in txn.splits))

    def test_bank_rules_transaction_override_beats_merchant_default(self) -> None:
        csv_text = "\n".join(
            [
                "Date,Description,Amount,Reference",
                "03/01/2026,Starbucks #1234,-5.00,A1",
                "03/02/2026,Starbucks #5678,-6.25,A2",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "card.csv"
            path.write_text(csv_text, encoding="utf-8")
            statement = parse_bank_statement_file(path)

        bank_import = BankImportSpec(account_guid="guid-card", statement_paths=(str(path),))
        merchant_key = bank_merchant_key("Starbucks #1234")
        mapping = MappingConfig(
            bank_merchant_accounts={merchant_key: "guid-exp-coffee"},
            bank_txn_account_overrides={"bank:guid-card:A2": "guid-exp-meals"},
        )
        transactions, warnings, _match_results, category_results = build_bank_transactions(
            ((bank_import, (statement,)),),
            mapping,
            (),
        )

        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(category_results), 2)
        by_dedupe = {result.bank_dedupe_key: result for result in category_results}
        self.assertEqual(by_dedupe["bank:guid-card:A1"].mapping_source, "merchant")
        self.assertEqual(by_dedupe["bank:guid-card:A2"].mapping_source, "transaction")
        self.assertEqual(by_dedupe["bank:guid-card:A2"].mapped_account_guid, "guid-exp-meals")
        txn_by_id = {txn.txn_id: txn for txn in transactions}
        self.assertTrue(any(split.account_guid == "guid-exp-coffee" for split in txn_by_id["A1"].splits))
        self.assertTrue(any(split.account_guid == "guid-exp-meals" for split in txn_by_id["A2"].splits))


if __name__ == "__main__":
    unittest.main()
