"""Workflow layer: named, editable, multi-step agent pipelines stored as data.

A *workflow* is a JSON file in config/workflows/. Each node names an existing
agent (from orchestrator.AGENTS) plus a step instruction and dependencies.

The system can rewrite its own workflows on request:
    propose_edit(...) -> validate(...) -> version the old file -> commit(...)
Old versions are kept in config/workflows/versions/ so any edit is reversible.

Token/cost control: each node may carry an optional "model" override so trivial
steps run on a small fast model and only the heavy step uses a large one.
"""

import json
import time
import copy
import re
import inspect
import contextlib
from pathlib import Path

from . import config
from .orchestrator import AGENTS, DEFAULT_AGENT

_ROOT = Path(__file__).resolve().parent.parent
WF_DIR = _ROOT / "config" / "workflows"
VERSIONS_DIR = WF_DIR / "versions"
SCHEMA_PATH = _ROOT / "config" / "workflow.schema.json"


# ── Load / list ───────────────────────────────────────────────────────────── #
def list_workflows() -> list:
    out = []
    if not WF_DIR.exists():
        return out
    for p in sorted(WF_DIR.glob("*.json")):
        try:
            wf = json.loads(p.read_text())
        except Exception:
            continue
        out.append({
            "id": wf.get("id", p.stem),
            "version": wf.get("version", 1),
            "description": wf.get("description", ""),
            "nodes": len(wf.get("nodes", [])),
        })
    return out


def get(workflow_id: str):
    p = WF_DIR / f"{workflow_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ── Validation ──────────────────────────────────────────────────────────────#
def _load_schema():
    try:
        return json.loads(SCHEMA_PATH.read_text())
    except Exception:
        return None


def validate(wf) -> tuple:
    """Returns (ok, message). Works with or without the `jsonschema` package:
    always runs minimal structural checks (incl. agent names), and adds full
    JSON-schema validation when the library is available."""
    if not isinstance(wf, dict) or "id" not in wf or "nodes" not in wf:
        return False, "workflow must be an object with 'id' and 'nodes'"
    if not isinstance(wf["nodes"], list) or not wf["nodes"]:
        return False, "'nodes' must be a non-empty list"
    valid_agents = set(AGENTS.keys())
    seen = set()
    for n in wf["nodes"]:
        if not isinstance(n, dict) or "id" not in n or "agent" not in n:
            return False, f"each node needs 'id' and 'agent': {n}"
        if n["id"] in seen:
            return False, f"duplicate node id: {n['id']}"
        seen.add(n["id"])
        if n["agent"] not in valid_agents:
            return False, f"unknown agent '{n['agent']}' (valid: {sorted(valid_agents)})"
    for n in wf["nodes"]:
        for dep in n.get("depends_on", []):
            if dep not in seen:
                return False, f"node '{n['id']}' depends on missing node '{dep}'"

    schema = _load_schema()
    if schema is not None:
        try:
            import jsonschema  # optional
            jsonschema.validate(wf, schema)
        except ImportError:
            pass  # minimal checks above already passed
        except Exception as e:
            return False, f"schema validation failed: {e}"
    return True, "ok"


# ── Self-editing ──────────────────────────────────────────────────────────── #
def _extract_json(raw: str):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def propose_edit(workflow_id: str, edit_request: str, provider):
    """Ask the model for the full updated workflow JSON. In mock mode (or when
    the workflow doesn't exist yet) it performs a deterministic structured edit
    so the propose->validate->commit->version cycle is demoable with no API key."""
    current = get(workflow_id)

    if current is None or config.PROVIDER == "mock":
        wf = copy.deepcopy(current) if current else {"id": workflow_id, "nodes": []}
        wf.setdefault("nodes", [])
        wf["version"] = int(wf.get("version", 0)) + 1
        wf.setdefault("description", f"Workflow {workflow_id}")
        last = wf["nodes"][-1]["id"] if wf["nodes"] else None
        wf["nodes"].append({
            "id": f"step_{len(wf['nodes']) + 1}",
            "agent": DEFAULT_AGENT,
            "instruction": (edit_request.strip()[:300] or "Additional step."),
            "depends_on": [last] if last else [],
        })
        return wf

    system = (
        "You edit workflow JSON for a multi-agent system. "
        "Output ONLY the COMPLETE updated workflow as valid JSON — no prose, no fences. "
        f"Valid agent names: {', '.join(AGENTS.keys())}. "
        "Always increment the integer 'version'. Keep node ids unique and depends_on referencing real nodes."
    )
    prompt = (
        f"Current workflow:\n{json.dumps(current, indent=2)}\n\n"
        f"Requested change: {edit_request}\n\n"
        "Return the full updated workflow JSON."
    )
    raw = await provider.complete(
        [{"role": "user", "content": prompt}],
        system=system, temperature=0.1, max_tokens=1500,
    )
    return _extract_json(raw)


def commit(workflow_id: str, new_wf: dict) -> dict:
    ok, msg = validate(new_wf)
    if not ok:
        return {"ok": False, "error": msg}
    new_wf.setdefault("id", workflow_id)
    WF_DIR.mkdir(parents=True, exist_ok=True)
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    old = get(workflow_id)
    if old is not None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (VERSIONS_DIR / f"{workflow_id}.v{old.get('version', 0)}.{stamp}.json").write_text(
            json.dumps(old, indent=2)
        )
    (WF_DIR / f"{workflow_id}.json").write_text(json.dumps(new_wf, indent=2))
    return {"ok": True, "id": workflow_id, "version": new_wf.get("version"),
            "nodes": len(new_wf.get("nodes", []))}


async def edit(workflow_id: str, edit_request: str, provider) -> dict:
    """Full self-edit cycle: propose -> validate -> version -> commit."""
    proposed = await propose_edit(workflow_id, edit_request, provider)
    if not proposed:
        return {"ok": False, "error": "could not parse a valid workflow from the model output"}
    return commit(workflow_id, proposed)


def versions(workflow_id: str) -> list:
    if not VERSIONS_DIR.exists():
        return []
    return sorted(p.name for p in VERSIONS_DIR.glob(f"{workflow_id}.v*.json"))


def rollback(workflow_id: str, version_filename: str) -> dict:
    src = VERSIONS_DIR / version_filename
    if not src.exists() or src.parent != VERSIONS_DIR:
        return {"ok": False, "error": "version not found"}
    try:
        wf = json.loads(src.read_text())
    except Exception as e:
        return {"ok": False, "error": f"could not read version: {e}"}
    return commit(workflow_id, wf)


# ── Execution ─────────────────────────────────────────────────────────────── #
def _topo(nodes: list) -> list:
    """Order nodes so dependencies run first. Leftover nodes (broken deps) are
    appended rather than dropped."""
    done, order = set(), []
    for _ in range(len(nodes) + 1):
        progressed = False
        for n in nodes:
            if n["id"] in done:
                continue
            if all(d in done for d in n.get("depends_on", [])):
                order.append(n)
                done.add(n["id"])
                progressed = True
        if not progressed:
            break
    for n in nodes:
        if n["id"] not in done:
            order.append(n)
    return order


@contextlib.contextmanager
def _model_override(model):
    """Temporarily point the active provider at a different model for one step.
    (Single-user/local assistant: steps run sequentially, so this is safe here.)"""
    if not model:
        yield
        return
    prev = config.MODEL
    config.MODEL = model
    try:
        yield
    finally:
        config.MODEL = prev


async def run(workflow_id: str, message: str, history: list, provider, user_profile=None):
    """Execute a named workflow as an ordered agent pipeline, yielding the same
    event dicts the orchestrator uses so it streams in the existing UI."""
    wf = get(workflow_id)
    if wf is None:
        yield {"type": "token", "text": f"⚠️ workflow '{workflow_id}' not found"}
        yield {"type": "done"}
        return

    order = _topo(wf.get("nodes", []))
    yield {
        "type": "route",
        "agent": order[0]["agent"] if order else DEFAULT_AGENT,
        "agents": [n["agent"] for n in order],
        "workflow": workflow_id,
    }

    outputs = {}
    for i, node in enumerate(order):
        is_last = (i == len(order) - 1)
        agent_obj = AGENTS.get(node["agent"], AGENTS[DEFAULT_AGENT])
        run_fn = agent_obj["run"]

        # Build this node's message: original request + step instruction + upstream context only.
        upstream = "\n\n".join(
            f"[{d}]:\n{outputs.get(d, '')}" for d in node.get("depends_on", []) if outputs.get(d)
        )
        node_msg = message
        if node.get("instruction"):
            node_msg += f"\n\n[STEP INSTRUCTION] {node['instruction']}"
        if upstream:
            node_msg += f"\n\n[CONTEXT FROM PRIOR STEPS]\n{upstream}"

        sig = inspect.signature(run_fn).parameters
        kwargs = {}
        if "user_profile" in sig:
            kwargs["user_profile"] = user_profile

        yield {"type": "step", "text": f"Step '{node['id']}' → {node['agent']}"}

        with _model_override(node.get("model")):
            if is_last:
                try:
                    async for ev in run_fn(provider, node_msg, history, **kwargs):
                        yield ev
                except Exception as e:
                    yield {"type": "token", "text": f"⚠️ {node['id']} error: {e}"}
            else:
                chunks = []
                try:
                    async for ev in run_fn(provider, node_msg, history, **kwargs):
                        if ev.get("type") == "token":
                            chunks.append(ev.get("text", ""))
                        elif ev.get("type") == "sources":
                            yield ev  # surface intermediate sources to the UI
                except Exception as e:
                    chunks.append(f"[{node['id']} error: {e}]")
                outputs[node["id"]] = "".join(chunks)

    yield {"type": "done"}
