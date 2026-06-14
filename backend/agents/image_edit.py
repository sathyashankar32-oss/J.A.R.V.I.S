"""Image editing agent — modifies an uploaded image per user instructions.

Uses Gemini's image generation model with input-image support so the
original composition, colours, and content are preserved and only the
requested changes are applied.
"""

import httpx

from .. import config
from .base import event

DESCRIPTION = (
    "Edits or modifies an uploaded/attached image according to user instructions. "
    "Use when the user has attached an image and wants to change it: add text, "
    "change background, adjust colours, add overlays, remove elements, change style, etc. "
    "Preserves the original image structure and only applies the requested changes."
)

_BASE       = "https://generativelanguage.googleapis.com/v1beta"
_EDIT_MODEL = "gemini-2.0-flash-exp-image-generation"


async def run(provider, message, history, image_data: str = None):
    if not image_data:
        yield event("token", text=(
            "⚠ No image attached. Use the 📎 button next to the input box to upload an image, "
            "then describe what you'd like to change."
        ))
        return

    if not config.GEMINI_API_KEY:
        yield event("token", text="⚠ Image editing requires a Gemini API key. Add one in the Provider panel.")
        return

    yield event("step", text="Reading your image and instructions…")

    # Parse data-URL → mime + raw base64
    if image_data.startswith("data:"):
        header, b64 = image_data.split(";base64,", 1)
        mime = header.replace("data:", "").strip() or "image/jpeg"
    else:
        mime, b64 = "image/jpeg", image_data

    instruction = (
        f"Edit this image following these instructions exactly: {message}\n\n"
        "Rules:\n"
        "- Apply ONLY what is asked. Change nothing else.\n"
        "- Preserve the original composition, lighting, colours, and style.\n"
        "- Keep all people/objects exactly as they are unless specifically asked to change them.\n"
        "- Output the full image at the same resolution and aspect ratio."
    )

    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": instruction},
                {"inlineData": {"mimeType": mime, "data": b64}},
            ],
        }],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }

    yield event("step", text=f"Applying edits with {_EDIT_MODEL}…")
    url = f"{_BASE}/models/{_EDIT_MODEL}:generateContent?key={config.GEMINI_API_KEY}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(url, json=body)
        if not resp.is_success:
            try:
                err = resp.json()
            except Exception:
                err = resp.text[:400]
            yield event("token", text=f"⚠ Gemini image edit failed (HTTP {resp.status_code}): {err}")
            return
        data = resp.json()

    parts = (
        data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
    )

    text_buf = []
    for part in parts:
        if "text" in part and part["text"].strip():
            text_buf.append(part["text"])
        inline = part.get("inlineData", {})
        out_mime = inline.get("mimeType", "")
        if out_mime.startswith("image/") and inline.get("data"):
            if text_buf:
                yield event("token", text=" ".join(text_buf).strip() + "\n\n")
            yield event("image",
                        url=f"data:{out_mime};base64,{inline['data']}",
                        alt=f"Edited: {message[:80]}")
            return

    if text_buf:
        yield event("token", text="\n".join(text_buf))
    else:
        yield event("token", text=(
            "⚠ Gemini did not return an edited image. "
            "Try rephrasing your instruction or use a simpler edit request."
        ))
