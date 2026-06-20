"""Server-side gallery of generated images.

Each item: {id, src, prompt, ts, admin}. Data-URL images are decoded to files and
served via /api/gallery/img/<id.ext>; remote URLs are stored by reference.
Admin-session images are flagged admin=true and only listed via the password-gated
admin endpoint, so they're not exposed on the open gallery.
"""

import base64
import json
import re
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DIR = _ROOT / "data" / "gallery"
INDEX = DIR / "index.json"

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/webp": "webp", "image/gif": "gif"}


def _dir() -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR


def _load() -> list:
    try:
        return json.loads(INDEX.read_text())
    except Exception:
        return []


def _save(items: list) -> None:
    _dir()
    INDEX.write_text(json.dumps(items))


def add(url: str, prompt: str = "", admin: bool = False) -> dict:
    _dir()
    iid = uuid.uuid4().hex[:16]
    src = url
    if isinstance(url, str) and url.startswith("data:"):
        m = re.match(r"data:([^;]+);base64,(.*)$", url, re.S)
        if m:
            ext = _EXT.get(m.group(1), "png")
            try:
                (DIR / f"{iid}.{ext}").write_bytes(base64.b64decode(m.group(2)))
                src = f"/api/gallery/img/{iid}.{ext}"
            except Exception:
                src = url
    items = _load()
    entry = {"id": iid, "src": src, "prompt": (prompt or "")[:300],
             "ts": int(time.time() * 1000), "admin": bool(admin)}
    items.insert(0, entry)
    _save(items)
    return entry


def list_items(admin: bool = False) -> list:
    return [e for e in _load() if bool(e.get("admin", False)) == bool(admin)]


def img_path(name: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
    p = _dir() / safe
    return p if (safe and p.exists()) else None


def delete(iid: str) -> dict:
    iid = re.sub(r"[^A-Za-z0-9]", "", iid or "")
    items, kept, removed = _load(), [], None
    for e in items:
        if e.get("id") == iid:
            removed = e
        else:
            kept.append(e)
    _save(kept)
    if removed and str(removed.get("src", "")).startswith("/api/gallery/img/"):
        fname = re.sub(r"[^A-Za-z0-9_.-]", "", removed["src"].split("/")[-1])
        try:
            (DIR / fname).unlink()
        except Exception:
            pass
    return {"ok": True, "id": iid}
