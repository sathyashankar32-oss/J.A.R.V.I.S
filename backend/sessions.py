"""Server-side session storage.

Each chat session is saved as one JSON file in data/sessions/<id>.json so it
persists across restarts and is shared by any browser/device that reaches this
server. Single-user/local scale — plain files, no database needed.

A session looks like:
  { "id": "...", "title": "...", "updated": <ms>, "messages": [ {role, content, ...} ] }
"""

import json
import re
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DIR = _ROOT / "data" / "sessions"


def _store() -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR


def _safe_id(sid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", sid or "")[:80]


def list_sessions() -> list:
    """Lightweight list for the sidebar (no message bodies)."""
    out = []
    for p in _store().glob("*.json"):
        try:
            s = json.loads(p.read_text())
        except Exception:
            continue
        out.append({
            "id": s.get("id", p.stem),
            "title": s.get("title", ""),
            "updated": s.get("updated", 0),
            "messages": len(s.get("messages", [])),
            "admin": bool(s.get("admin", False)),
        })
    out.sort(key=lambda x: x.get("updated", 0), reverse=True)
    return out


def get(sid: str):
    sid = _safe_id(sid)
    if not sid:
        return None
    p = _store() / f"{sid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save(sid: str, session: dict) -> dict:
    sid = _safe_id(sid)
    if not sid:
        return {"ok": False, "error": "invalid id"}
    session = dict(session or {})
    session["id"] = sid
    if not session.get("updated"):
        session["updated"] = int(time.time() * 1000)
    try:
        (_store() / f"{sid}.json").write_text(json.dumps(session))
        return {"ok": True, "id": sid, "updated": session["updated"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete(sid: str) -> dict:
    sid = _safe_id(sid)
    p = _store() / f"{sid}.json"
    try:
        if p.exists():
            p.unlink()
        return {"ok": True, "id": sid}
    except Exception as e:
        return {"ok": False, "error": str(e)}
