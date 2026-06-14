"""FastAPI application.

Endpoints:
  GET  /              -> the chat UI (single-page frontend)
  GET  /api/config    -> active provider/model (shown in the UI)
  GET  /api/agents    -> the agent registry (shown in the sidebar)
  POST /api/chat      -> streams orchestrated response as newline-delimited JSON
"""

import json
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, orchestrator

app = FastAPI(title="J.A.R.V.I.S")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND   = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[Message] = []
    agent: Optional[str] = None           # pin a single agent (skips routing)
    agents: Optional[List[str]] = None    # run multiple agents as a pipeline
    memory_context: Optional[str] = None  # injected cross-session context
    user_profile: Optional[str] = None    # personality profile built from past prompts
    image_data: Optional[str] = None      # base64 data-URL of an attached image


class ConfigUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    ollama_host: Optional[str] = None
    ollama_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None


@app.get("/")
async def index():
    return FileResponse(FRONTEND)


@app.get("/api/config")
async def get_config():
    return config.summary()


@app.post("/api/config")
async def update_config(req: ConfigUpdate):
    from . import config as cfg
    from . import providers
    if req.provider and req.provider in cfg.PROVIDERS_LIST:
        cfg.PROVIDER = req.provider
        if not req.model:
            cfg.MODEL = cfg.DEFAULT_MODELS.get(req.provider, "mock-1")
    if req.model:
        cfg.MODEL = req.model
    if req.api_key:
        p = cfg.PROVIDER
        if p == "groq":
            cfg.GROQ_API_KEY = req.api_key
        elif p == "gemini":
            cfg.GEMINI_API_KEY = req.api_key
        elif p == "openai":
            cfg.OPENAI_API_KEY = req.api_key
    if req.ollama_host:
        cfg.OLLAMA_HOST = req.ollama_host.rstrip("/")
    if req.ollama_api_key is not None:
        cfg.OLLAMA_API_KEY = req.ollama_api_key
    if req.openai_base_url:
        cfg.OPENAI_BASE_URL = req.openai_base_url.rstrip("/")
    providers.invalidate_cache()
    cfg.persist()   # write back to .env so choices survive server restarts
    return cfg.summary()


@app.get("/api/agents")
async def get_agents():
    return {"agents": orchestrator.registry()}


@app.get("/api/models")
async def list_models(provider: str = "ollama"):
    """Return selectable models for the given provider."""
    import httpx

    GROQ_MODELS = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "llama3-70b-8192",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ]
    GEMINI_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]
    OPENAI_FALLBACK = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ]

    if provider == "groq":
        return {"models": GROQ_MODELS}
    if provider == "gemini":
        return {"models": GEMINI_MODELS}
    if provider == "mock":
        return {"models": ["mock-1"]}

    # Ollama: fetch live from local or cloud server
    if provider == "ollama":
        import re

        def _param_billions(model_info: dict) -> float:
            """Parse parameter count from Ollama model metadata, largest first."""
            # details.parameter_size is the most reliable field: "70.6B", "8B", etc.
            ps = (model_info.get("details") or {}).get("parameter_size", "")
            m = re.search(r"(\d+(?:\.\d+)?)\s*[Bb]", ps)
            if m:
                return float(m.group(1))
            # Fall back to parsing the tag in the model name: "llama3.3:70b"
            name = model_info.get("name", "")
            m = re.search(r"(\d+(?:\.\d+)?)b", name.lower())
            if m:
                return float(m.group(1))
            return 0.0

        try:
            headers = {}
            if config.OLLAMA_API_KEY:
                headers["Authorization"] = f"Bearer {config.OLLAMA_API_KEY}"
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{config.OLLAMA_HOST}/api/tags",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            model_infos = data.get("models", [])
            # Sort largest-parameter model first so the UI auto-selects it
            model_infos.sort(key=_param_billions, reverse=True)
            models = [m["name"] for m in model_infos]
            return {"models": models or ["gemma3:4b"]}
        except Exception as e:
            return {"models": ["gemma3:4b"], "error": str(e)}

    # OpenAI / OpenAI-compatible: try to fetch live model list from the base URL
    if provider == "openai":
        base = config.OPENAI_BASE_URL.rstrip("/")
        key  = config.OPENAI_API_KEY
        if base and key:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(
                        f"{base}/models",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                # OpenAI-compatible /v1/models returns {"data": [{"id": "..."}]}
                models = [m["id"] for m in data.get("data", []) if m.get("id")]
                if models:
                    return {"models": sorted(models)}
            except Exception:
                pass
        # Fallback to hardcoded list if live fetch fails or no key yet
        return {"models": OPENAI_FALLBACK}

    return {"models": []}


@app.get("/api/gemini-models")
async def list_gemini_models():
    """Diagnostic — lists all Gemini models available for your API key."""
    import httpx
    if not config.GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models?pageSize=100&key={config.GEMINI_API_KEY}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
    if not resp.is_success:
        return {"error": resp.status_code, "body": resp.text}
    data = resp.json()
    names = [m["name"] for m in data.get("models", [])]
    image_models = [n for n in names if any(k in n for k in ("imagen", "flash", "vision", "image"))]
    return {"all_models": names, "image_related": image_models}


class TitleRequest(BaseModel):
    messages: List[Message]


@app.post("/api/title")
async def generate_title(req: TitleRequest):
    """Generate a short AI summary title for a conversation."""
    import asyncio
    from .providers import get_provider

    msgs = [m.model_dump() for m in req.messages]

    # Mock provider: derive title from first user message heuristically
    if config.PROVIDER == "mock":
        first = next((m["content"] for m in msgs if m["role"] == "user"), "Session")
        return {"title": first[:52] + ("…" if len(first) > 52 else "")}

    try:
        provider = get_provider()
        raw = await asyncio.wait_for(
            provider.complete(
                msgs,
                system=(
                    "Summarise this conversation in 5 words or fewer — a sharp, descriptive title. "
                    "Reply with ONLY the title. No quotes, no punctuation at the end."
                ),
                temperature=0.3,
                max_tokens=18,
            ),
            timeout=12.0,
        )
        title = raw.strip().strip("\"'").strip()
        return {"title": title or "Untitled Session"}
    except Exception:
        first = next((m["content"] for m in msgs if m["role"] == "user"), "Session")
        return {"title": first[:52] + ("…" if len(first) > 52 else "")}


class PersonalityRequest(BaseModel):
    messages: List[Message]  # recent user messages (user role only is fine)


@app.post("/api/personality")
async def build_personality(req: PersonalityRequest):
    """Analyse recent prompts and return a short personality/preference profile."""
    import asyncio
    from .providers import get_provider

    user_msgs = [m.content for m in req.messages if m.role == "user"]
    if not user_msgs:
        return {"profile": ""}

    combined = "\n".join(f"- {m}" for m in user_msgs[-40:])  # last 40 user turns

    system = (
        "You are analysing a user's conversation history to build a brief personality profile "
        "that will help a personal AI assistant remember who they are.\n\n"
        "Write 4–8 bullet points covering:\n"
        "• Their name (if ever mentioned)\n"
        "• Topics, projects, or domains they care about\n"
        "• Communication style (formal/casual/technical)\n"
        "• Any stated preferences or dislikes\n"
        "• Skills or profession clues\n\n"
        "Be factual and specific — only include things clearly evidenced in the messages. "
        "Do NOT invent. Reply with bullet points only, no preamble."
    )

    try:
        # Prefer Gemini for this as it's fast and good at summarisation
        from .providers import gemini_provider
        if config.GEMINI_API_KEY:
            provider = gemini_provider()
        else:
            provider = get_provider()

        raw = await asyncio.wait_for(
            provider.complete(
                [{"role": "user", "content": f"Here are the user's recent messages:\n{combined}"}],
                system=system,
                temperature=0.3,
                max_tokens=300,
            ),
            timeout=20.0,
        )
        return {"profile": raw.strip()}
    except Exception as e:
        return {"profile": "", "error": str(e)}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    history = [m.model_dump() for m in req.history]

    async def gen():
        async for ev in orchestrator.handle(
            req.message,
            history,
            agent_override=req.agent,
            agents_override=req.agents,
            memory_context=req.memory_context,
            user_profile=req.user_profile,
            image_data=req.image_data,
        ):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")
