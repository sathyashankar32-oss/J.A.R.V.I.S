"""Newsletter subscriber store.

Captures each email the moment it is submitted (event-driven — NOT a daily batch),
so no signup is ever missed. Stored durably in data/subscribers.json, deduped by
email. Exportable to .xlsx (or .csv fallback if openpyxl isn't installed) for the
monthly newsletter.
"""

import json
import re
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DIR = _ROOT / "data"
FILE = DIR / "subscribers.json"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load() -> list:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return []


def _save(items: list) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(items, indent=2))


def add(email: str, source: str = "") -> dict:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        return {"ok": False, "error": "invalid email"}
    items = _load()
    if any(s.get("email") == email for s in items):
        return {"ok": True, "duplicate": True, "count": len(items)}
    items.append({"email": email, "ts": int(time.time() * 1000), "source": (source or "")[:80]})
    _save(items)
    return {"ok": True, "count": len(items)}


def list_all() -> list:
    return _load()


def export(path: str = None) -> dict:
    """Write the subscriber list to .xlsx (or .csv fallback). Returns {ok, format, path, count}."""
    items = _load()
    DIR.mkdir(parents=True, exist_ok=True)
    xlsx_path = Path(path) if path else (DIR / "subscribers.xlsx")
    try:
        from openpyxl import Workbook
        from datetime import datetime, timezone
        wb = Workbook()
        ws = wb.active
        ws.title = "Subscribers"
        ws.append(["Email", "Subscribed (UTC)", "Source"])
        for s in items:
            ts = s.get("ts", 0)
            when = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""
            ws.append([s.get("email", ""), when, s.get("source", "")])
        wb.save(xlsx_path)
        return {"ok": True, "format": "xlsx", "path": str(xlsx_path), "count": len(items)}
    except ImportError:
        import csv
        csv_path = DIR / "subscribers.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Email", "Subscribed (ms UTC)", "Source"])
            for s in items:
                w.writerow([s.get("email", ""), s.get("ts", ""), s.get("source", "")])
        return {"ok": True, "format": "csv", "path": str(csv_path), "count": len(items)}
