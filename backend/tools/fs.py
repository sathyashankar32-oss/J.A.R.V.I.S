"""Workspace-scoped filesystem tools for the coding agent.

Every operation is confined to a single root (config.AGENT_WORKSPACE). Paths are
resolved and verified to live inside that root, so the agent cannot read or write
anywhere else on disk. Functions never raise to the caller — they return error
strings / dicts — so a bad path can't crash a chat turn.
"""

import os
import difflib
from pathlib import Path

from .. import config

_SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".DS_Store"}


def root() -> Path:
    r = Path(os.path.expanduser(config.AGENT_WORKSPACE)).resolve()
    r.mkdir(parents=True, exist_ok=True)
    return r


def _safe(path: str):
    """Resolve `path` under the workspace root; return None if it escapes."""
    r = root()
    p = (r / path).resolve()
    try:
        p.relative_to(r)
    except ValueError:
        return None
    return p


def list_tree(max_entries: int = 200) -> str:
    r = root()
    lines = []
    for p in sorted(r.rglob("*")):
        if any(part in _SKIP for part in p.relative_to(r).parts):
            continue
        rel = p.relative_to(r)
        depth = len(rel.parts) - 1
        lines.append(f"{'  ' * depth}{rel.name}{'/' if p.is_dir() else ''}")
        if len(lines) >= max_entries:
            lines.append("… (truncated)")
            break
    return "\n".join(lines) or "(workspace is empty)"


def read_file(path: str, max_chars: int = 8000) -> str:
    p = _safe(path)
    if p is None:
        return f"ERROR: path is outside the workspace: {path}"
    if not p.exists() or not p.is_file():
        return f"ERROR: file not found: {path}"
    try:
        data = p.read_text(errors="replace")
    except Exception as e:
        return f"ERROR: {e}"
    if len(data) > max_chars:
        return data[:max_chars] + "\n… (truncated)"
    return data


def write_file(path: str, content: str) -> dict:
    p = _safe(path)
    if p is None:
        return {"ok": False, "path": path, "error": "path is outside the workspace"}
    try:
        existed = p.exists()
        old = p.read_text(errors="replace") if existed else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {
            "ok": True, "path": path,
            "action": "modified" if existed else "created",
            "bytes": len(content), "old": old, "new": content,
        }
    except Exception as e:
        return {"ok": False, "path": path, "error": str(e)}


def diff(old: str, new: str, path: str, max_lines: int = 200) -> str:
    d = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    ))
    if len(d) > max_lines:
        d = d[:max_lines] + [f"… (+{len(d) - max_lines} more diff lines)"]
    return "\n".join(d)
