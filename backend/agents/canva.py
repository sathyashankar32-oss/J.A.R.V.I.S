"""Canva agent — creates a real design in your Canva account via the Canva
Connect API and returns an editable link plus a PNG export.

Requires a Canva Connect access token (config.CANVA_API_TOKEN). Without one it
returns clear setup steps instead of erroring.

Note on scope: the Canva Connect API does not expose free-form text-to-image AI
generation. This agent creates/opens a real Canva design (which you finish in the
editor) and exports it as a PNG. For pure AI image generation with no setup, use
the `image` agent instead.

Endpoints (verified against canva.dev docs):
  POST /v1/designs            create a design
  POST /v1/exports            start a PNG export job
  GET  /v1/exports/{jobId}    poll until success, returns download URL(s)
"""

import asyncio

import httpx

from .. import config
from .base import event

DESCRIPTION = (
    "Creates a design in your Canva account (presentation, doc, or whiteboard) and "
    "returns an editable Canva link plus a PNG export. Use for 'make/design this in Canva'."
)

# Connect API 'preset' design types that are safe to request.
_PRESETS = {
    "presentation": ["presentation", "slides", "deck", "slide", "pitch"],
    "doc":          ["doc", "document", "letter", "report", "resume", "cv"],
    "whiteboard":   ["whiteboard", "brainstorm", "diagram", "mind map", "flow"],
}

_SETUP = (
    "**Canva isn't connected yet.** I've wired the Canva Connect API in — it needs a token:\n\n"
    "1. Go to **canva.dev** → *Your integrations* → create a **Connect API** integration.\n"
    "2. Add scopes: `design:content:write`, `design:meta:read`, `design:content:read`.\n"
    "3. Run the OAuth flow to get an **access token** (Canva's docs walk through it).\n"
    "4. In JARVIS, set it: `//admin` → then I can store it, or put `CANVA_API_TOKEN=...` "
    "in your `.env` and restart.\n\n"
    "_Tip: for instant image generation with zero setup, just ask for an image normally — "
    "that uses the free `image` agent._"
)


def _pick_preset(message: str) -> str:
    text = message.lower()
    for preset, words in _PRESETS.items():
        if any(w in text for w in words):
            return preset
    return "presentation"


async def run(provider, message, history):
    token = config.CANVA_API_TOKEN
    if not token:
        yield event("token", text=_SETUP)
        return

    base = config.CANVA_API_BASE
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    preset = _pick_preset(message)
    title = (message.strip()[:80] or "JARVIS design")

    yield event("step", text=f"Creating a Canva {preset}…")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            r = await client.post(
                f"{base}/v1/designs",
                headers=headers,
                json={"design_type": {"type": "preset", "name": preset}, "title": title},
            )
            if not r.is_success:
                yield event("token", text=f"⚠️ Canva create-design failed (HTTP {r.status_code}): {r.text[:400]}")
                return
            design = r.json().get("design") or r.json()
    except Exception as e:
        yield event("token", text=f"⚠️ Could not reach Canva: {e}")
        return

    design_id = design.get("id")
    urls = design.get("urls") or {}
    edit_url = urls.get("edit_url") or design.get("edit_url")

    line = f"Created a Canva **{preset}** — “{title}”."
    if edit_url:
        line += f"\n\n**Open / edit in Canva:** {edit_url}"
    yield event("token", text=line)

    if not design_id:
        return

    # Export a PNG so the result shows inline.
    yield event("step", text="Exporting a PNG…")
    try:
        png_url = await _export_png(base, headers, design_id)
        if png_url:
            yield event("image", url=png_url, alt=title)
        else:
            yield event("step", text="Export did not return an image (the editable link above still works).")
    except Exception as e:
        yield event("step", text=f"Export skipped — {str(e)[:160]}")


async def _export_png(base: str, headers: dict, design_id: str):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        r = await client.post(
            f"{base}/v1/exports",
            headers=headers,
            json={"design_id": design_id, "format": {"type": "png"}},
        )
        r.raise_for_status()
        job = r.json().get("job") or r.json()
        job_id = job.get("id")
        if not job_id:
            return None

        for _ in range(20):  # up to ~40s
            await asyncio.sleep(2)
            s = await client.get(f"{base}/v1/exports/{job_id}", headers=headers)
            s.raise_for_status()
            j = s.json().get("job") or s.json()
            status = j.get("status")
            if status == "success":
                out = j.get("urls") or []
                return out[0] if out else None
            if status == "failed":
                return None
    return None
