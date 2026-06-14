"""Video agent — generates short video clips via Google Veo 3 (Gemini API).

Pipeline:
  1. Refine the user's prompt using Gemini 2.5 Flash.
  2. Submit to Veo 3 Fast which returns a long-running operation.
  3. Poll the operation until complete (up to 5 minutes).
  4. Return the video as a base64 data-URL for inline playback.
"""

import asyncio

import httpx

from .. import config
from ..providers import gemini_provider
from .base import event

DESCRIPTION = (
    "Generates a short video clip from a description using Google Veo 3. "
    "Use for 'generate a video of', 'create a clip showing', 'animate', or any video request."
)

_REFINE_SYSTEM = (
    "Turn the user's request into a vivid video generation prompt of 1-2 sentences. "
    "Describe the scene, motion, camera angle, lighting, and visual style. "
    "Return ONLY the prompt text, nothing else."
)

_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Fast variant for quicker turnaround; fall back to standard if unavailable
_VEO_MODELS = [
    "veo-3.0-fast-generate-001",
    "veo-3.0-generate-001",
    "veo-2.0-generate-001",
]

_POLL_INTERVAL = 5    # seconds between polls
_POLL_TIMEOUT  = 300  # max seconds to wait


async def run(provider, message, history):
    if not config.GEMINI_API_KEY:
        yield event("token", text="⚠ GEMINI_API_KEY is required for video generation.")
        return

    # Step 1: Refine prompt
    yield event("step", text="Refining video prompt with Gemini 2.5 Flash")
    prompt = message
    gem = gemini_provider()
    try:
        refined = await gem.complete(
            [{"role": "user", "content": message}],
            system=_REFINE_SYSTEM,
            temperature=0.7,
            max_tokens=180,
        )
        if refined.strip():
            prompt = refined.strip()
    except Exception:
        pass

    yield event("step", text=f"Prompt: {prompt[:80]}{'…' if len(prompt) > 80 else ''}")

    # Step 2: Submit to Veo 3
    last_err = None
    for model in _VEO_MODELS:
        yield event("step", text=f"Submitting to {model}")
        try:
            op_name = await _submit_veo(prompt, model, config.GEMINI_API_KEY)
            break
        except Exception as e:
            last_err = e
            yield event("step", text=f"{model} unavailable — {e}")
    else:
        yield event("token", text=f"⚠ All Veo models failed: {last_err}")
        return

    # Step 3: Poll until done
    yield event("step", text="Generating video — this takes 1-3 minutes…")
    elapsed = 0
    while elapsed < _POLL_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
        try:
            done, result = await _poll_operation(op_name, config.GEMINI_API_KEY)
        except Exception as e:
            yield event("token", text=f"⚠ Polling error: {e}")
            return
        if done:
            break
        if elapsed % 30 == 0:
            yield event("step", text=f"Still generating… ({elapsed}s elapsed)")
    else:
        yield event("token", text="⚠ Video generation timed out after 5 minutes.")
        return

    # Step 4: Extract video
    try:
        video_url, mime = _extract_video(result)
    except Exception as e:
        yield event("token", text=f"⚠ Could not extract video from response: {e}\n\nRaw: {str(result)[:300]}")
        return

    yield event("token", text=f"Generated with **Veo 3**: _{prompt}_\n\n")
    yield event("video", url=video_url, mime=mime, alt=prompt)


async def _submit_veo(prompt: str, model: str, api_key: str) -> str:
    """POST to Veo and return the operation name."""
    url = f"{_BASE}/models/{model}:predictLongRunning?key={api_key}"
    body = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "durationSeconds": 8,
            "aspectRatio": "16:9",
            "personGeneration": "allow_adult",
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

    op = data.get("name") or data.get("operationName")
    if not op:
        raise ValueError(f"No operation name in response: {data}")
    return op


async def _poll_operation(op_name: str, api_key: str):
    """Poll a long-running operation. Returns (done, response_dict)."""
    # op_name is like "models/veo-3.0-fast-generate-001/operations/abc123"
    url = f"{_BASE}/{op_name}?key={api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    return data.get("done", False), data


def _extract_video(result: dict):
    """Pull video bytes or URI out of the completed operation response."""
    # Try response.predictions path
    response = result.get("response", {})
    predictions = response.get("predictions") or result.get("predictions") or []

    for pred in predictions:
        # Direct bytes
        if "bytesBase64Encoded" in pred:
            mime = pred.get("mimeType", "video/mp4")
            return f"data:{mime};base64,{pred['bytesBase64Encoded']}", mime
        # Nested video object
        video = pred.get("video") or pred.get("videoBytes") or {}
        if isinstance(video, dict):
            if "bytesBase64Encoded" in video:
                mime = video.get("mimeType", "video/mp4")
                return f"data:{mime};base64,{video['bytesBase64Encoded']}", mime
            if "uri" in video:
                return video["uri"], "video/mp4"
        # Direct URI
        if "uri" in pred:
            return pred["uri"], "video/mp4"

    raise ValueError(f"No video found in result: {list(result.keys())}")
