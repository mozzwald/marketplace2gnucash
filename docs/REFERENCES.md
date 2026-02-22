# References (GnuCash bindings + safety)

## GnuCash Python bindings
- Python bindings overview (official): https://code.gnucash.org/docs/STABLE/python_bindings_page.html
- Python bindings examples index (official): https://code.gnucash.org/docs/STABLE/group__python__bindings__examples.html
- GnuCash wiki notes on Python bindings: https://wiki.gnucash.org/wiki/Python_Bindings
- GnuCash guide chapter mentioning Python bindings: https://www.gnucash.org/docs/v5/C/gnucash-guide/ch_python_bindings.html

## Lock / backup behavior
- GnuCash guide: backup/lock/auto-save file behavior (includes .LCK discussion): https://code.gnucash.org/website/docs/v2.4/C/gnucash-guide/basics-backup1.html

## Qt / PySide6
- Qt for Python (PySide6) docs: https://doc.qt.io/qtforpython-6/
- PySide6 on PyPI (packaging/licensing info): https://pypi.org/project/PySide6/

## “Do not forget” facts
- Bindings open/write the book directly; GnuCash does not need to be running.
- Refuse to write if the book is locked/open.
- Always backup before writing.
- Use Decimal for money.
- Tax handling: Etsy tax is excluded from income and expense; only used to compute IncomeBase.
- Refunds are separate transactions and do not reduce income.
- Multi-book: key config and dedupe by book_id (root account GUID).
