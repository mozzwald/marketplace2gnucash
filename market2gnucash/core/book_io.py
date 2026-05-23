from __future__ import annotations

import gzip
import shutil
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from market2gnucash.core.models import AccountRecord, BookInfo

_LOCK_SUFFIXES = (".LCK", ".LNK", ".lock")


def detect_book_locks(book_path: str | Path) -> tuple[str, ...]:
    path = Path(book_path)
    candidates = [Path(f"{path}{suffix}") for suffix in _LOCK_SUFFIXES]
    lock_files = tuple(str(candidate) for candidate in candidates if candidate.exists())
    return lock_files


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag.split(":", 1)[-1]


def _child_text(element: ET.Element, local_name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == local_name:
            return (child.text or "").strip()
    return None


def _extract_accounts(xml_root: ET.Element) -> tuple[AccountRecord, ...]:
    accounts_by_guid: dict[str, dict[str, str | None]] = {}

    for element in xml_root.iter():
        if _local_name(element.tag) != "account":
            continue

        guid = _child_text(element, "id")
        if not guid:
            continue
        accounts_by_guid[guid] = {
            "name": _child_text(element, "name") or "",
            "type": _child_text(element, "type") or "",
            "parent": _child_text(element, "parent"),
        }

    root_guid = None
    for guid, account in accounts_by_guid.items():
        if account["type"] == "ROOT":
            root_guid = guid
            break
    if not root_guid:
        raise ValueError("Could not locate ROOT account GUID in book XML")

    def full_name(guid: str) -> str:
        names: list[str] = []
        current_guid: str | None = guid
        while current_guid:
            account = accounts_by_guid.get(current_guid)
            if not account:
                break
            if account["type"] == "ROOT":
                break
            names.append(account["name"] or "")
            current_guid = account["parent"]
        return ":".join(reversed([name for name in names if name]))

    records: list[AccountRecord] = []
    for guid, account in accounts_by_guid.items():
        records.append(
            AccountRecord(
                guid=guid,
                name=account["name"] or "",
                account_type=account["type"] or "",
                parent_guid=account["parent"],
                full_name=full_name(guid),
            )
        )

    records.sort(key=lambda item: (item.full_name, item.guid))
    return tuple(records)


def load_book_info(book_path: str | Path) -> BookInfo:
    path = Path(book_path)
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("rb") as raw_handle:
        magic = raw_handle.read(2)

    if magic == b"\x1f\x8b":
        with gzip.open(path, "rb") as gz_handle:
            xml_root = ET.parse(gz_handle).getroot()
    else:
        xml_root = ET.parse(path).getroot()

    accounts = _extract_accounts(xml_root)
    root_accounts = [account for account in accounts if account.account_type == "ROOT"]
    if not root_accounts:
        raise ValueError("Could not find ROOT account in GnuCash XML")

    book_id = root_accounts[0].guid
    locks = detect_book_locks(path)

    return BookInfo(
        path=str(path),
        book_id=book_id,
        lock_files=locks,
        accounts=accounts,
    )


def create_timestamped_backup(book_path: str | Path) -> Path:
    source = Path(book_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = source.with_suffix(source.suffix + f".{timestamp}.bak")
    shutil.copy2(source, backup_path)
    return backup_path
