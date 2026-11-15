"""
storage.py
==========
JSON file storage layer for the EV Charging Station Allocation System.

Responsibilities
----------------
1. Create the data/ folder and all JSON files automatically.
2. Read and write JSON safely (thread-safe and crash-safe).
3. Recover automatically if a JSON file is missing or corrupted.
4. Generate unique, incrementing IDs (EV-0001, REQ-0001, ...).

No SQL and no database server are used. Only plain JSON files.
"""

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# The data folder sits next to this file, no matter where you run Python from.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Logical name -> file path
FILES = {
    "evs": DATA_DIR / "evs.json",            # registered electric vehicles
    "stations": DATA_DIR / "stations.json",  # charging stations (chargers)
    "requests": DATA_DIR / "requests.json",  # every charging request + status
    "history": DATA_DIR / "history.json",    # completed / cancelled sessions
    "meta": DATA_DIR / "meta.json",          # ID counters
}

# Value used when a file is created for the first time (or recreated).
# Lists for records, a dictionary for meta.
DEFAULTS = {
    "evs": list,
    "stations": list,
    "requests": list,
    "history": list,
    "meta": lambda: {"counters": {}},
}

# One re-entrant lock protects every file operation in this process.
# RLock lets the same thread acquire it again (needed by next_id -> write).
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    """Current local time as a string, e.g. '2026-11-05 14:30:00'."""
    return datetime.now().strftime(TIME_FORMAT)


def parse_time(value: str) -> datetime:
    """Convert a string created by now_str() back into a datetime."""
    return datetime.strptime(value, TIME_FORMAT)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _validate_key(key: str) -> None:
    if key not in FILES:
        raise KeyError(
            f"Unknown storage key '{key}'. Valid keys: {', '.join(FILES)}"
        )


def _atomic_write(path: Path, data) -> None:
    """
    Write JSON safely.

    The data is first written to a temporary file in the same folder and then
    swapped into place with os.replace(). If the program crashes halfway,
    the original file is never left half-written or corrupted.
    """
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(data, tmp_file, indent=2, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)  # atomic on Windows, macOS and Linux
    except Exception:
        # Clean up the temp file if anything went wrong
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _quarantine_corrupt_file(path: Path) -> None:
    """Rename a corrupted file so no data is silently lost."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.corrupt_{stamp}.json")
    try:
        os.replace(path, backup)
    except OSError:
        pass  # if renaming fails we simply overwrite below


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def init_storage() -> None:
    """
    Make sure the data/ folder and every JSON file exist.
    Safe to call as many times as you like. Existing data is never touched.
    """
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for key, path in FILES.items():
            if not path.exists():
                _atomic_write(path, DEFAULTS[key]())


def read_data(key: str):
    """
    Load and return the contents of a JSON file.

    key is one of: 'evs', 'stations', 'requests', 'history', 'meta'.
    Returns a list (or a dict for 'meta'). If the file is missing it is
    created. If it is corrupted it is backed up and recreated empty.
    """
    _validate_key(key)
    with _lock:
        init_storage()
        path = FILES[key]
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            _quarantine_corrupt_file(path)
            fresh = DEFAULTS[key]()
            _atomic_write(path, fresh)
            return fresh


def write_data(key: str, data) -> None:
    """Replace the entire contents of a JSON file with `data`."""
    _validate_key(key)
    with _lock:
        init_storage()
        _atomic_write(FILES[key], data)


def append_record(key: str, record: dict) -> dict:
    """Add one record to the end of a list-type file and return it."""
    with _lock:
        records = read_data(key)
        records.append(record)
        write_data(key, records)
        return record


def next_id(prefix: str) -> str:
    """
    Generate a unique ID such as 'EV-0001', 'REQ-0007' or 'HIS-0003'.
    The counter is stored in meta.json so IDs never repeat, even after a
    restart or after records are deleted.
    """
    with _lock:
        meta = read_data("meta")
        counters = meta.setdefault("counters", {})
        counters[prefix] = counters.get(prefix, 0) + 1
        write_data("meta", meta)
        return f"{prefix}-{counters[prefix]:04d}"


def reset_all_data() -> None:
    """Erase every record and counter and start fresh (used by the Reset button)."""
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for key, path in FILES.items():
            _atomic_write(path, DEFAULTS[key]())


# ---------------------------------------------------------------------------
# Quick self-test: run   python storage.py   to check everything works
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_storage()
    print(f"Data folder : {DATA_DIR}")
    for name, file_path in FILES.items():
        status = "OK" if file_path.exists() else "MISSING"
        print(f"  {name:<9} -> {file_path.name:<14} [{status}]")

    print("Sample IDs  :", next_id("TEST"), next_id("TEST"))
    print("Storage is working correctly.")