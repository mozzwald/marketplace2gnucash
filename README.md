# market2gnucash

Cross-platform Python 3.11+ desktop GUI (PySide6) to import Etsy/eBay marketplace exports plus bank/card statements into an existing GnuCash XML book using the GnuCash Python bindings.

## Features

- Single-window tabbed workflow:
  - `Book`
  - `Inputs`
  - `Marketplace Mapping`
  - `Bank/Card Mapping`
  - `Preview`
  - `Import`
  - `Settings`
- Book safety checks:
  - Reads book metadata and root GUID (`book_id`)
  - Detects lock sidecar files (`.LCK`, `.LNK`, `.lock`) and blocks writes
  - Creates timestamped backup before any write
- Idempotent imports:
  - Per-book dedupe keys stored in SQLite
  - Duplicate planned transactions are marked and skipped
  - Stable row fingerprints plus occurrence numbering prevent duplicate imports when refreshed monthly exports are overwritten with newer copies
- Multi-book support:
  - Inputs + mapping configuration persisted per `book_id`
- Bank/card import support:
  - CSV and OFX/QFX statements
  - One statement directory per account import bundle
  - CSV profile/mapping support for headerless or nonstandard CSV layouts
- Decimal-only monetary handling (no float arithmetic)

## Required Inputs

### Etsy

Add one monthly export pair for every Etsy month that can contribute rows to the preview/import window. Each pair requires:

1. `etsy_statement_YYYY_M.csv`
2. `EtsySoldOrdersYYYY-M.csv`

Recommended workflow:

- Keep stable monthly filenames and overwrite/refresh the current month files as Etsy updates them.
- When a weekly import spans a month boundary, include both monthly export pairs in the same Etsy account input.
- If selected files are renamed or moved, reselect them before previewing. Directory detection can rebuild the monthly export list from files that still use Etsy's standard filenames.

### eBay

- A dedicated directory containing one or more eBay Transaction report CSV files
- Preview imports every CSV file in the selected directory
- Refreshed/overlapping reports are safe to keep in the directory; duplicate rows are detected by content-based dedupe keys

### Bank / Card

- One import bundle per bank or card account
- Select a dedicated statement directory per account; preview imports every supported statement file in that directory
- Supported formats:
  - CSV
  - OFX / QFX
- Nonstandard or headerless CSV layouts can be configured once per bank/card bundle through the app's CSV mapping dialog
- Renaming files inside the selected directory does not require reselecting them; moved directories must be reselected

## Accounting Rules Implemented

### Clearing accounts

User selects one clearing account per marketplace (asset/current asset style):

- Etsy clearing
- eBay clearing

### Etsy sales

- One transaction per Order ID
- Clearing split = order net proceeds from statement order-tied rows
- Income split = `-(SoldOrders.OrderTotal - StatementTax)`
- Fee splits mapped by Etsy key
- Etsy tax is used only to derive IncomeBase, not posted as tax account splits
- Shipping charged to buyer remains inside sales income

### Etsy listing fees

- One transaction per listing-fee statement row (no aggregation)

### Etsy refunds

- Refunds are separate transactions
- Refunds do not reduce income
- Clearing decreases
- Refund expense increases
- Fee refund rows with positive net reverse fee expense (sign-aware)

### Etsy mapping keys

- Exact: `etsy:{Type}:{Title}`
- Wildcard fallback: `etsy:Fee:Transaction fee:*`
- Shipping fee remains distinct: `etsy:Fee:Transaction fee: Shipping`

### eBay sales

- One transaction per order
- Clearing split = order net amount
- Income split = `-(item subtotal + shipping charged to buyer)`
- Fee splits from non-zero fee columns, mapped by column name

### eBay refunds

- Refunds are separate transactions
- Refunds do not reduce income
- Clearing decreases
- Refund expense increases
- Fee adjustments reverse fee expenses when report signs indicate credits

### eBay tax

- `Seller collected tax` is considered informationally in planning
- `eBay collected tax` is ignored for posting logic

### Bank / card planning

- Bank/card transactions are imported against the selected GnuCash asset or liability account for each statement bundle
- Marketplace payout/charge candidates can be matched against bank/card rows in preview
- Internal transfers between bank/card accounts can be matched or manually overridden in `Bank/Card Mapping`
- Merchant defaults and per-transaction account overrides are supported for unmatched bank/card activity

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Dev extras (lint/test tooling):

```bash
pip install -e ".[dev]"
```

## GnuCash Python Bindings

GnuCash bindings are generally installed from OS/GnuCash packages, not PyPI.

If bindings are unavailable, preview and mapping can still be generated, but import/write will fail and be blocked.

## Run

```bash
market2gnucash
```

or:

```bash
python -m market2gnucash.app
```

## Standalone Build

The project is set up for a PyInstaller `onedir` build.

Prerequisites:

1. Build with a Python interpreter that can already import `gnucash`.
2. Run the bootstrap build script from the repo root:

```bash
./build.sh
```

What `build.sh` does:

- Selects `PYTHON` if provided, otherwise tries `python3.12`, `python3.11`, then `python3`
- Creates `.venv/` with `--system-site-packages` if needed so OS-installed `gnucash` bindings stay visible
- Upgrades `pip`, `setuptools`, and `wheel`
- Installs the project with build extras
- Fails fast if the chosen interpreter cannot import `gnucash`
- Runs the standalone PyInstaller build

If the machine has multiple Python installs and only one can import the GnuCash bindings, point the script at it:

```bash
PYTHON=/path/to/python ./build.sh
```

If you already created `.venv/` without system site packages, remove it and rerun `./build.sh`.

Output:

- App bundle directory: `dist/market2gnucash/`
- Executable:
  - Linux/macOS: `dist/market2gnucash/market2gnucash`
  - Windows: `dist/market2gnucash/market2gnucash.exe`

Notes:

- This is a `onedir` bundle, not a single-file executable. That is intentional because PySide6 and GnuCash bindings are less brittle in directory form.
- The build helper fails fast if `PyInstaller` or `gnucash` are not importable in the build environment.
- The PyInstaller spec file is `market2gnucash.spec`.

## Safe Usage Checklist

1. Close GnuCash before importing.
2. Open the correct `.gnucash` file in the `Book` tab.
3. Confirm lock status shows not locked.
4. In `Inputs`, add marketplace account bundles and any bank/card statement bundles.
5. Configure marketplace clearing, income, refund, and fee mappings in `Marketplace Mapping`.
6. Review bank/card matches, transfer matches, and category overrides in `Bank/Card Mapping`.
7. Run dry-run in `Preview` and resolve warnings, duplicates, or deferred items.
8. Import in `Import` after confirming the planned transactions (backup is created automatically first).

## Project Structure

- `market2gnucash/core/parsers.py`: Etsy/eBay parsers plus bank/card CSV and OFX/QFX parsing
- `market2gnucash/core/rules.py`: planning rules -> planned transactions/splits
- `market2gnucash/core/planner.py`: dedupe-aware dry-run planning
- `market2gnucash/core/config_store.py`: per-book JSON config
- `market2gnucash/core/dedupe_store.py`: per-book SQLite dedupe
- `market2gnucash/core/carryover_store.py`: pending marketplace payout/charge carryover state
- `market2gnucash/core/book_io.py`: book metadata, lock detection, backup
- `market2gnucash/core/gnucash_writer.py`: bindings write implementation
- `market2gnucash/ui/`: PySide6 tabs and window

## Tests

The parsing/rules/dedupe tests do not require GnuCash bindings.

```bash
python -m unittest discover -s tests -q
```

Optional integration write test (skipped by default):

```bash
RUN_GNUCASH_INTEGRATION=1 python -m unittest tests/test_gnucash_writer_integration.py -q
```
