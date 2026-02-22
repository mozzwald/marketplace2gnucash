from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from market2gnucash.core.models import PlannedSplit, PlannedTransaction

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class WriteResult:
    written_keys: tuple[str, ...]


def _decimal_to_fraction(amount: Decimal) -> tuple[int, int]:
    normalized = amount.normalize()
    sign, digits, exponent = normalized.as_tuple()
    int_value = 0
    for digit in digits:
        int_value = (int_value * 10) + digit
    if sign:
        int_value = -int_value

    if exponent >= 0:
        return int_value * (10**exponent), 1
    denominator = 10 ** (-exponent)
    return int_value, denominator


class GnuCashWriter:
    def __init__(self, book_path: str | Path) -> None:
        self.book_path = Path(book_path)

    def write_transactions(
        self,
        transactions: list[PlannedTransaction],
        progress_cb: ProgressCallback | None = None,
    ) -> WriteResult:
        if not transactions:
            return WriteResult(written_keys=())

        try:
            from gnucash import GncNumeric, Session, SessionOpenMode, Split, Transaction
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "GnuCash Python bindings are not available in this environment"
            ) from exc

        uri = f"xml://{self.book_path.resolve()}"
        session = Session(uri, SessionOpenMode.SESSION_NORMAL_OPEN)
        written: list[str] = []

        try:
            book = session.book
            root_account = book.get_root_account()
            accounts_by_guid = {
                account.GetGUID().to_string(): account
                for account in [root_account, *root_account.get_descendants()]
            }

            total = len(transactions)
            for index, planned in enumerate(transactions, start=1):
                transaction = Transaction(book)
                transaction.BeginEdit()
                try:
                    transaction.SetDescription(planned.description)
                    transaction.SetDate(planned.date.day, planned.date.month, planned.date.year)

                    split_accounts = [
                        accounts_by_guid.get(split.account_guid or "")
                        for split in planned.splits
                        if split.account_guid
                    ]
                    currency = None
                    for account in split_accounts:
                        if account is None:
                            continue
                        commodity = account.GetCommodity()
                        if commodity is not None:
                            currency = commodity
                            break
                    if currency is not None:
                        transaction.SetCurrency(currency)

                    for split in planned.splits:
                        self._append_split(
                            book=book,
                            split=split,
                            transaction=transaction,
                            accounts_by_guid=accounts_by_guid,
                            gnc_numeric_type=GncNumeric,
                            split_type=Split,
                            tx_label=planned.description,
                        )

                    transaction.CommitEdit()
                    written.append(planned.dedupe_key)
                except Exception:
                    if hasattr(transaction, "RollbackEdit"):
                        transaction.RollbackEdit()
                    raise

                if progress_cb:
                    progress_cb(index, total, planned.description)

            session.save()
            return WriteResult(written_keys=tuple(written))
        finally:
            session.end()
            session.destroy()

    @staticmethod
    def _append_split(
        *,
        book,
        split: PlannedSplit,
        transaction,
        accounts_by_guid,
        gnc_numeric_type,
        split_type,
        tx_label: str,
    ) -> None:
        if not split.account_guid:
            raise RuntimeError(f"Transaction {tx_label} has split with no account mapping")

        account = accounts_by_guid.get(split.account_guid)
        if account is None:
            raise RuntimeError(
                f"Could not find account GUID {split.account_guid} for transaction {tx_label}"
            )

        raw_num, raw_denom = _decimal_to_fraction(split.amount)
        numeric = gnc_numeric_type(raw_num, raw_denom)

        book_split = split_type(book)
        book_split.SetParent(transaction)
        book_split.SetAccount(account)
        book_split.SetMemo(split.memo)
        book_split.SetValue(numeric)
        book_split.SetAmount(numeric)
