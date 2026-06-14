"""Image agent — generates images via multiple sources.

Pipeline:
  1. Refine the user's prompt with Gemini (or active provider).
  2. Try Gemini native image modality (requires paid/allowlisted key).
  3. Try Pollinations (flux model, backend download to avoid browser re-request rate limits).
  4. Try Stable Horde  — free, distributed volunteer-GPU network, no key needed.
  5. Try Lexica search — find an existing image that matches the prompt.
"""

import asyncio
import base64
import random
import urllib.parse

import httpx

from .. import config
from ..providers import gemini_provider
from .base import event

DESCRIPTION = (
    "Generates a high-quality image from a description. "
    "Use for pictures, illustrations, art, logos, and 'draw / generate an image of' requests."
)

_REFINE_SYSTEM = (
    "Turn the user's request into a single vivid image-generation prompt of one or two "
    "sentences. Describe subject, style, lighting, and composition. Return ONLY the prompt text."
)

_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Gemini image-generation models (all require paid/allowlisted access).
_IMAGE_MODELS = [
    "gemini-2.0-flash-exp-image-generation",
    "gemini-2.0-flash-exp",
    "gemini-2.0-flash-preview-image-generation",
]

_HORDE_ANON_KEY = "0000000000"  # anonymous — free, lower priority
_HORDE_BASE = "https://stablehorde.net/api/v2"


async def run(provider, message, history):
    # ------------------------------------------------------------------ #
    # Step 1: Refine prompt                                                #
    # ------------------------------------------------------------------ #
    yield event("step", text="Refining image prompt…")
    prompt = message

    if config.GEMINI_API_KEY:
        gem = gemini_provider()
        try:
            refined = await gem.complete(
                [{"role": "user", "content": message}],
                system=_REFINE_SYSTEM,
                temperature=0.7,
                max_tokens=150,
            )
            if refined.strip():
                prompt = refined.strip()
        except Exception:
            pass
    elif config.PROVIDER != "mock":
        try:
            refined = await provider.complete(
                [{"role": "user", "content": message}],
                system=_REFINE_SYSTEM,
                temperature=0.7,
                max_tokens=150,
            )
            if refined.strip():
                prompt = refined.strip()
        except Exception:
            pass

    short_prompt = prompt[:300] if len(prompt) > 300 else prompt

    # ------------------------------------------------------------------ #
    # Step 2: Gemini native image modality                                 #
    # ------------------------------------------------------------------ #
    if config.GEMINI_API_KEY:
        for model in _IMAGE_MODELS:
            yield event("step", text=f"Trying {model}…")
            try:
                data_url, mime = await _gemini_image(prompt, model, config.GEMINI_API_KEY)
                yield event("token", text=f"Generated with **{model}**: _{short_prompt}_\n\n")
                yield event("image", url=data_url, alt=short_prompt)
                return
            except Exception as e:
                err = str(e)
                if "404" in err or "paid" in err.lower() or "not found" in err.lower():
                    continue
                yield event("step", text=f"{model} error — {err[:150]}")

    # ------------------------------------------------------------------ #
    # Step 3: Pollinations (backend download — avoids browser re-request) #
    # ------------------------------------------------------------------ #
    seed = random.randint(1, 2**31)
    poll_url = (
        f"https://image.pollinations.ai/prompt/{urllib.parse.quote(short_prompt)}"
        f"?width=1024&height=1024&model=flux&nologo=true&nofeed=true&seed={seed}"
    )
    yield event("step", text="Generating with Pollinations Flux (this can take up to 90 s)…")
    try:
        data_url = await _download_as_base64(poll_url, timeout=120.0)
        yield event("token", text=f"Generated with **Pollinations · Flux**: _{short_prompt}_\n\n")
        yield event("image", url=data_url, alt=short_prompt)
        return
    except Exception as e:
        yield event("step", text=f"Pollinations failed — {str(e)[:200]}")

    # ------------------------------------------------------------------ #
    # Step 4: Stable Horde — free distributed volunteer GPU network       #
    # ------------------------------------------------------------------ #
    yield event("step", text="Trying Stable Horde (free distributed GPUs — may take 1–3 min)…")
    try:
        data_url = await _stable_horde(short_prompt)
        yield event("token", text=f"Generated with **Stable Horde**: _{short_prompt}_\n\n")
        yield event("image", url=data_url, alt=short_prompt)
        return
    except Exception as e:
        yield event("step", text=f"Stable Horde failed — {str(e)[:200]}")

    # ------------------------------------------------------------------ #
    # Step 5: Lexica search — find an existing matching image             #
    # ------------------------------------------------------------------ #
    yield event("step", text="Searching Lexica for a matching image…")
    try:
        lexica_url = f"https://lexica.art/api/v1/search?q={urllib.parse.quote(short_prompt)}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(lexica_url)
            if resp.is_success:
                results = resp.json().get("images", [])
                if results:
                    img_url = results[0].get("src", "")
                    if img_url:
                        data_url = await _download_as_base64(img_url, timeout=30.0)
                        yield event("token", text=f"Found via **Lexica**: _{short_prompt}_\n\n")
                        yield event("image", url=data_url, alt=short_prompt)
                        return
                else:
                    yield event("step", text="Lexica returned no results for this prompt")
            else:
                yield event("step", text=f"Lexica returned HTTP {resp.status_code}")
    except Exception as e:
        yield event("step", text=f"Lexica failed — {str(e)[:200]}")

    yield event("token", text=(
        "⚠ All image sources failed.\n\n"
        "Check the step trace above for the specific errors. "
        "If Pollinations shows a timeout, just try again — it can be slow at peak times. "
        "If Stable Horde shows 'no workers', the network may be temporarily idle."
    ))


async def _download_as_base64(url: str, timeout: float = 60.0) -> str:
    """Fetch an image URL and return it as a base64 data-URL."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not content_type.startswith("image/"):
            raise ValueError(f"Non-image response ({content_type}): {resp.text[:300]}")
        b64 = base64.b64encode(resp.content).decode()
        return f"data:{content_type};base64,{b64}"


async def _stable_horde(prompt: str) -> str:
    """Submit to Stable Horde and poll until the image is ready.

    Stable Horde is a free distributed network of volunteer GPUs.
    Anonymous requests (apikey=0000000000) work but get lower priority.
    Typical wait: 30 s – 3 min depending on network load.
    Image is returned as base64 directly in the status response.
    """
    headers = {
        "apikey": _HORDE_ANON_KEY,
        "Content-Type": "application/json",
        "Client-Agent": "JARVIS:1.0:local",
    }
    payload = {
        "prompt": prompt,
        "params": {
            "n": 1,
            "width": 512,
            "height": 512,
            "steps": 25,
            "cfg_scale": 7.5,
            "sampler_name": "k_euler_a",
        },
        "r2": False,         # return base64 inline (not R2 URL)
        "shared": False,
        "slow_workers": True,  # include slower workers for better availability
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        sub = await client.post(
            f"{_HORDE_BASE}/generate/async",
            json=payload,
            headers=headers,
        )
        sub.raise_for_status()
        sub_data = sub.json()

    job_id = sub_data.get("id")
    if not job_id:
        raise ValueError(f"No job ID from Stable Horde: {sub_data}")

    # Poll for completion — up to 4 minutes
    for attempt in range(48):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            check = await client.get(
                f"{_HORDE_BASE}/generate/check/{job_id}",
                headers={"apikey": _HORDE_ANON_KEY, "Client-Agent": "JARVIS:1.0:local"},
            )
            check_data = check.json()

        if check_data.get("faulted"):
            raise ValueError("Stable Horde job faulted (no workers available)")

        if check_data.get("done"):
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                status = await client.get(
                    f"{_HORDE_BASE}/generate/status/{job_id}",
                    headers={"apikey": _HORDE_ANON_KEY, "Client-Agent": "JARVIS:1.0:local"},
                )
                status_data = status.json()

            gens = status_data.get("generations", [])
            if not gens:
                raise ValueError("Stable Horde returned empty generations list")

            img = gens[0].get("img", "")
            if not img:
                raise ValueError("Stable Horde generation has no image data")

            # `img` is either a data-URL already or raw base64
            if img.startswith("data:"):
                return img
            return f"data:image/png;base64,{img}"

        # Log queue position every ~30 s (every 6 polls)
        if attempt % 6 == 5:
            queue_pos = check_data.get("queue_position", "?")
            wait = check_data.get("wait_time", "?")
            _ = f"Still queued — position {queue_pos}, est. {wait} s remaining"

    raise ValueError("Stable Horde timed out after 4 minutes")


async def _gemini_image(prompt: str, model: str, api_key: str) -> tuple:
    """Call Gemini generateContent with IMAGE response modality."""
    url = f"{_BASE}/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(url, json=body)
        if not resp.is_success:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:400]
            raise ValueError(f"HTTP {resp.status_code}: {detail}")
        data = resp.json()

    parts = (
        data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
    )
    for part in parts:
        inline = part.get("inlineData", {})
        mime = inline.get("mimeType", "")
        if mime.startswith("image/") and inline.get("data"):
            return f"data:{mime};base64,{inline['data']}", mime

    raise ValueError(f"No image in response parts: {[list(p.keys()) for p in parts]}")
