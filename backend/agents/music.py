"""Music agent — generates music via Google Lyria 3 (Gemini API).

Pipeline:
  1. Refine the user's request into a music generation prompt.
  2. Submit to Lyria 3 Pro (long-running operation).
  3. Poll until complete.
  4. Return audio as a base64 data-URL for inline playback.
"""

import asyncio

import httpx

from .. import config
from ..providers import gemini_provider
from .base import event

DESCRIPTION = (
    "Generates original music from a description using Google Lyria 3. "
    "Use for 'create music', 'generate a song', 'compose', 'make a beat', or any music request."
)

_REFINE_SYSTEM = (
    "Turn the user's request into a music generation prompt. Describe the genre, mood, tempo, "
    "instruments, energy level, and any vocals. Be specific and vivid. "
    "Return ONLY the prompt text, nothing else."
)

_BASE = "https://generativelanguage.googleapis.com/v1beta"

_LYRIA_MODELS = [
    "lyria-3-pro-preview",
    "lyria-3-clip-preview",
]

_POLL_INTERVAL = 5
_POLL_TIMEOUT  = 240


async def run(provider, message, history):
    if not config.GEMINI_API_KEY:
        yield event("token", text="⚠ GEMINI_API_KEY is required for music generation.")
        return

    # Step 1: Refine prompt
    yield event("step", text="Crafting music prompt with Gemini 2.5 Flash")
    prompt = message
    gem = gemini_provider()
    try:
        refined = await gem.complete(
            [{"role": "user", "content": message}],
            system=_REFINE_SYSTEM,
            temperature=0.8,
            max_tokens=150,
        )
        if refined.strip():
            prompt = refined.strip()
    except Exception:
        pass

    yield event("step", text=f"Prompt: {prompt[:80]}{'…' if len(prompt) > 80 else ''}")

    # Step 2: Submit to Lyria
    last_err = None
    chosen_model = None
    for model in _LYRIA_MODELS:
        yield event("step", text=f"Submitting to {model}")
        try:
            op_name = await _submit_lyria(prompt, model, config.GEMINI_API_KEY)
            chosen_model = model
            break
        except Exception as e:
            last_err = e
            yield event("step", text=f"{model} unavailable — {e}")
    else:
        yield event("token", text=f"⚠ All Lyria models failed: {last_err}")
        return

    # Step 3: Poll
    yield event("step", text="Composing music — please wait…")
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
            yield event("step", text=f"Still composing… ({elapsed}s elapsed)")
    else:
        yield event("token", text="⚠ Music generation timed out.")
        return

    # Step 4: Extract audio
    try:
        audio_url, mime = _extract_audio(result)
    except Exception as e:
        yield event("token", text=f"⚠ Could not extract audio: {e}\n\nRaw: {str(result)[:300]}")
        return

    yield event("token", text=f"Composed with **{chosen_model}**: _{prompt}_\n\n")
    yield event("audio", url=audio_url, mime=mime, alt=prompt)


async def _submit_lyria(prompt: str, model: str, api_key: str) -> str:
    url = f"{_BASE}/models/{model}:predictLongRunning?key={api_key}"
    body = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

    op = data.get("name") or data.get("operationName")
    if not op:
        raise ValueError(f"No operation name: {data}")
    return op


async def _poll_operation(op_name: str, api_key: str):
    url = f"{_BASE}/{op_name}?key={api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    return data.get("done", False), data


def _extract_audio(result: dict):
    response = result.get("response", {})
    predictions = response.get("predictions") or result.get("predictions") or []

    for pred in predictions:
        if "bytesBase64Encoded" in pred:
            mime = pred.get("mimeType", "audio/wav")
            return f"data:{mime};base64,{pred['bytesBase64Encoded']}", mime
        audio = pred.get("audio") or pred.get("audioBytes") or {}
        if isinstance(audio, dict):
            if "bytesBase64Encoded" in audio:
                mime = audio.get("mimeType", "audio/wav")
                return f"data:{mime};base64,{audio['bytesBase64Encoded']}", mime
            if "uri" in audio:
                return audio["uri"], "audio/wav"
        if "uri" in pred:
            return pred["uri"], "audio/wav"

    raise ValueError(f"No audio found in result: {list(result.keys())}")
