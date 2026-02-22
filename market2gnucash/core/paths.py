from __future__ import annotations

import os
import platform
from pathlib import Path

_APP_NAME = "market2gnucash"


def app_data_dir() -> Path:
    override = os.environ.get("MARKET2GNUCASH_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    system = platform.system().lower()
    if system == "windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"

    path = base / _APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_json_path() -> Path:
    return app_data_dir() / "config.json"


def dedupe_db_path() -> Path:
    return app_data_dir() / "dedupe.sqlite3"
