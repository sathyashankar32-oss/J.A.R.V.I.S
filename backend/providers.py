"""Model provider abstraction.

Every provider exposes the same tiny interface:
    async stream(messages, system=None, temperature=0.7, max_tokens=1024) -> async iterator of text chunks
    async complete(...) -> str   (collects the stream)

This is what makes the agents portable: the same agent code runs against a
local Ollama model, a free Groq/Gemini key, or the built-in mock.
"""

import asyncio
import json
from typing import AsyncIterator, Optional

import httpx

from . import config


class Provider:
    name = "base"

    async def stream(self, messages, system=None, temperature=0.7, max_tokens=1024) -> AsyncIterator[str]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def complete(self, messages, system=None, temperature=0.7, max_tokens=1024) -> str:
        out = []
        async for chunk in self.stream(messages, system=system, temperature=temperature, max_tokens=max_tokens):
            out.append(chunk)
        return "".join(out)


# --------------------------------------------------------------------------- #
# Mock provider — no network, no key. Lets you see the full UI + orchestration
# instantly. Swap to a real provider to see genuine model output.
# --------------------------------------------------------------------------- #
class MockProvider(Provider):
    name = "mock"

    async def stream(self, messages, system=None, temperature=0.7, max_tokens=1024):
        label = "assistant"
        if system:
            low = system.lower()
            for key in ("coding", "writing", "research analyst", "solver", "math", "image"):
                if key in low:
                    label = key.split()[0]
                    break
        user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user = m.get("content", "")
                break
        snippet = (user[:90] + "…") if len(user) > 90 else user
        text = (
            f"**[mock · {label}]** This is a placeholder response so you can see the "
            f"interface and the orchestration working without any setup.\n\n"
            f"You asked: _{snippet}_\n\n"
            f"Switch `PROVIDER` in your `.env` to `ollama` (free, local) or add a free "
            f"`GROQ_API_KEY` / `GEMINI_API_KEY` to get a real answer from a live model. "
            f"The routing, streaming, research trace, and source list above are all real — "
            f"only the words in this paragraph are canned."
        )
        for token in _wordstream(text):
            await asyncio.sleep(0.012)
            yield token


def _wordstream(text: str):
    parts = text.split(" ")
    for i, p in enumerate(parts):
        yield (p if i == 0 else " " + p)


# --------------------------------------------------------------------------- #
# Ollama — fully free, runs locally. Install from ollama.com, then e.g.
#   ollama pull llama3.2
# --------------------------------------------------------------------------- #
class OllamaProvider:
    name = "ollama"

    async def stream(self, messages, system=None, temperature=0.7, max_tokens=1024):
        # Lift the cap for real generations (large code, long docs) while keeping
        # short utility calls (routing, titles, prompt-refine) small so they stay fast.
        # Clamp to a workable ceiling. Absurd values (e.g. a million) can't be
        # allocated by any model and make the request hang or fail outright.
        _CTX_CEILING = 131072
        num_predict = max(max_tokens, config.OLLAMA_MAX_TOKENS) if (max_tokens and max_tokens > 256) else max_tokens
        num_ctx = min(config.OLLAMA_NUM_CTX, _CTX_CEILING)
        num_predict = min(num_predict, num_ctx)  # output can't exceed the context
        payload = {
            "model": config.MODEL,
            "messages": _with_system(messages, system),
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            },
        }
        url = f"{config.OLLAMA_HOST}/api/chat"
        headers = {}
        if config.OLLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {config.OLLAMA_API_KEY}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = (data.get("message") or {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break


# --------------------------------------------------------------------------- #
# OpenAI-compatible streaming (covers Groq and any OpenAI-style gateway).
# --------------------------------------------------------------------------- #
class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str, name: str):
        self.base_url = base_url
        self.api_key = api_key
        self.name = name

    async def stream(self, messages, system=None, temperature=0.7, max_tokens=4096):
        if not self.api_key or not self.api_key.strip():
            raise ValueError(
                f"No API key set for {self.name}. "
                "Open the Provider panel, paste your key, and click Apply."
            )
        msgs = _with_system(messages, system)
        # Ensure messages alternate user/assistant (some providers reject consecutive same-role)
        msgs = _fix_alternation(msgs)
        payload = {
            "model": config.MODEL,
            "messages": msgs,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if not resp.is_success:
                    body = await resp.aread()
                    try:
                        detail = json.loads(body).get("error", {}).get("message", body.decode())
                    except Exception:
                        detail = body.decode()[:300]
                    raise ValueError(f"HTTP {resp.status_code} from {self.name}: {detail}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (obj.get("choices") or [{}])[0].get("delta", {})
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk


# --------------------------------------------------------------------------- #
# Gemini — free tier. Get a key at aistudio.google.com/apikey
# --------------------------------------------------------------------------- #

# Default model used when Gemini is a sidecar (research, writing, image agents).
# Pinned here so it stays valid when config.MODEL is a Groq/Ollama model name.
_GEMINI_SIDECAR_MODEL = "gemini-2.5-flash"


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str = None):
        # None → use config.MODEL (correct when Gemini IS the main provider).
        # Sidecar callers pass _GEMINI_SIDECAR_MODEL so config.MODEL never leaks.
        self._model = model

    async def stream(self, messages, system=None, temperature=0.7, max_tokens=1024):
        model = self._model or config.MODEL
        contents = []
        for m in messages:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
        body = {"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:streamGenerateContent?alt=sse&key={config.GEMINI_API_KEY}"
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            async with client.stream("POST", url, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for cand in obj.get("candidates", []):
                        for part in (cand.get("content") or {}).get("parts", []):
                            if part.get("text"):
                                yield part["text"]


def _with_system(messages, system):
    msgs = list(messages)
    if system:
        msgs = [{"role": "system", "content": system}] + msgs
    return msgs


def _fix_alternation(messages):
    """Ensure user/assistant messages strictly alternate.
    Some providers (Groq) reject consecutive messages of the same role.
    System messages are left in place; only user/assistant are merged."""
    result = []
    for msg in messages:
        if msg["role"] == "system":
            result.append(msg)
            continue
        if result and result[-1]["role"] == msg["role"] and msg["role"] != "system":
            # Merge with previous same-role message
            result[-1] = {
                "role": msg["role"],
                "content": result[-1]["content"] + "\n\n" + msg["content"],
            }
        else:
            result.append(dict(msg))
    return result


_cached: Optional[Provider] = None


def invalidate_cache():
    global _cached
    _cached = None


def get_provider() -> Provider:
    global _cached
    if _cached is not None and _cached.name == config.PROVIDER:
        return _cached
    p = config.PROVIDER
    if p == "ollama":
        _cached = OllamaProvider()
    elif p == "groq":
        _cached = OpenAICompatProvider(config.GROQ_BASE_URL, config.GROQ_API_KEY, "groq")
    elif p == "openai":
        _cached = OpenAICompatProvider(config.OPENAI_BASE_URL, config.OPENAI_API_KEY, "openai")
    elif p == "gemini":
        _cached = GeminiProvider()
    else:
        _cached = MockProvider()
    return _cached


def gemini_provider() -> GeminiProvider:
    """Returns a GeminiProvider pinned to the sidecar model.
    Used by research, writing, and image agents regardless of the main provider setting.
    Pinning the model prevents config.MODEL (which could be a Groq/Ollama name) from
    being injected into Gemini API URLs."""
    return GeminiProvider(model=_GEMINI_SIDECAR_MODEL)
