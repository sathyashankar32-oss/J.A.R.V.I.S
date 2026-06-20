"""Coding agent — reads and edits real files in the workspace.

Flow:
  1. (real models only) Ask which existing files it needs to read, then read them.
  2. Stream the answer. Any file the model emits as a ```file:<path>``` block is
     written to the workspace (scoped to config.AGENT_WORKSPACE).
  3. Report each created/modified file with a unified diff.

In mock mode it behaves like a normal code generator (no file writes), so the
UI still works with zero setup.
"""

import re

from .. import config
from ..tools import fs
from .base import event, stream_text

DESCRIPTION = (
    "Writes, debugs, and edits code. With a workspace configured it can READ and "
    "WRITE real files in your project (VS Code) and report a diff of every change."
)

# Fenced block whose info string is `file:<relative/path>` → write that file.
_FILE_BLOCK = re.compile(r"```file:(\S+)[ \t]*\r?\n(.*?)```", re.S)

_SYSTEM = """You are a senior software engineer editing the user's project directly.

WORKSPACE FILE TREE (this is the project root you can edit):
{tree}

To create or change a file, output its COMPLETE new contents in a fenced block whose
info string is `file:<relative/path>`. Example:

```file:src/utils.py
def add(a, b):
    return a + b
```

Rules:
- Always output the FULL file contents in a file: block — it overwrites that file.
- Use paths relative to the workspace root. Never use absolute paths or `..`.
- You may write multiple files in one reply. Briefly explain what you changed and,
  if relevant, how to run it.
- For pure explanations or debugging where no file should change, just answer
  normally with ordinary ``` code blocks (no `file:` prefix).
{readme}"""


async def run(provider, message, history):
    tree = fs.list_tree()

    # 1. Decide which files to read (skipped for mock — it has no real reasoning).
    file_ctx = ""
    if config.PROVIDER != "mock":
        try:
            ask = (
                f"Project files:\n{tree}\n\nTask: {message}\n\n"
                "List ONLY the existing files whose current contents you must read to do "
                "this correctly. Reply with lines like 'READ: path/to/file' and nothing "
                "else, or reply 'NONE'."
            )
            raw = await provider.complete(
                [{"role": "user", "content": ask}],
                system="You select which files to open. Be minimal and precise.",
                temperature=0.0, max_tokens=200,
            )
            for p in re.findall(r"READ:\s*(\S+)", raw)[:8]:
                content = fs.read_file(p)
                file_ctx += f"\n\n=== {p} ===\n{content}"
                yield event("step", text=f"Read {p}")
        except Exception:
            pass

    readme = (
        f"\nCurrent contents of the files you need:\n{file_ctx}" if file_ctx
        else f"\nWorkspace root on disk: {fs.root()}"
    )
    system = _SYSTEM.format(tree=tree, readme=readme)

    # 2. Stream the answer, buffering it so we can extract file blocks afterwards.
    buf = []
    async for ev in stream_text(provider, system, message, history, temperature=0.3):
        if ev.get("type") == "token":
            buf.append(ev["text"])
        yield ev

    # 3. Persist any file: blocks and report diffs.
    full = "".join(buf)
    writes = list(_FILE_BLOCK.finditer(full))
    if not writes:
        return

    reported = []
    for m in writes:
        path = m.group(1).strip()
        content = m.group(2)
        if content.endswith("\n"):
            content = content[:-1]  # drop the newline before the closing fence
        res = fs.write_file(path, content)
        if res.get("ok"):
            yield event("step", text=f"{res['action'].title()} {path} ({res['bytes']} bytes)")
            reported.append((path, res["action"], fs.diff(res.get("old", ""), res.get("new", ""), path)))
        else:
            yield event("step", text=f"Could not write {path}: {res.get('error')}")

    if reported:
        lines = ["\n\n---\n**Saved to your workspace:**"]
        for path, action, _ in reported:
            lines.append(f"\n- `{path}` — {action}")
        diffs = "\n\n".join(f"```diff\n{d}\n```" for _, _, d in reported if d)
        yield event("token", text="\n".join(lines) + (("\n\n" + diffs) if diffs else ""))
