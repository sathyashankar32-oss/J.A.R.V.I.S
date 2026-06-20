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

from . import config, orchestrator, workflows
from . import sessions as sess
from . import gallery
from . import subscribers as subs

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
    memory_context: Optional[str] = None       # injected cross-session context
    user_profile: Optional[str] = None         # personality profile built from past prompts
    image_data: Optional[str] = None           # base64 data-URL of an attached image
    session_personality: Optional[str] = None  # admin session-only personality override
    workflow: Optional[str] = None              # run a specific saved workflow by id


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


@app.get("/api/workflows")
async def list_workflows_endpoint():
    """List saved workflows (named, editable agent pipelines)."""
    return {"workflows": workflows.list_workflows()}


@app.get("/api/workflows/{workflow_id}")
async def get_workflow_endpoint(workflow_id: str):
    from fastapi import HTTPException
    wf = workflows.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return wf


# ── Sessions (server-side chat history) ───────────────────────────────────── #
class SessionBody(BaseModel):
    title: Optional[str] = ""
    messages: List[dict] = []
    updated: Optional[int] = None
    admin: bool = False


@app.get("/api/sessions")
async def api_list_sessions():
    return {"sessions": sess.list_sessions()}


@app.get("/api/sessions/{sid}")
async def api_get_session(sid: str):
    s = sess.get(sid)
    if s is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.put("/api/sessions/{sid}")
async def api_put_session(sid: str, body: SessionBody):
    return sess.save(sid, body.model_dump())


@app.delete("/api/sessions/{sid}")
async def api_delete_session(sid: str):
    return sess.delete(sid)


# ── Gallery (generated images) ────────────────────────────────────────────── #
class GalleryBody(BaseModel):
    url: str
    prompt: Optional[str] = ""
    admin: bool = False


@app.post("/api/gallery")
async def api_gallery_add(body: GalleryBody):
    return gallery.add(body.url, body.prompt, body.admin)


@app.get("/api/gallery")
async def api_gallery_list():
    # Open gallery only ever exposes non-admin images.
    return {"items": gallery.list_items(admin=False)}


@app.get("/api/gallery/img/{name}")
async def api_gallery_img(name: str):
    p = gallery.img_path(name)
    if p is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(str(p))


@app.delete("/api/gallery/{iid}")
async def api_gallery_delete(iid: str):
    return gallery.delete(iid)


# ── Newsletter subscribers ────────────────────────────────────────────────── #
class SubscribeBody(BaseModel):
    email: str
    source: Optional[str] = ""


@app.post("/api/subscribe")
async def api_subscribe(body: SubscribeBody):
    """Public: your website's signup form posts here. Captured immediately."""
    return subs.add(body.email, body.source or "")


@app.get("/api/subscribers/export")
async def api_subscribers_export(token: str = ""):
    from fastapi import HTTPException
    if token != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="admin token required")
    res = subs.export()
    fname = "subscribers.xlsx" if res.get("format") == "xlsx" else "subscribers.csv"
    return FileResponse(res["path"], filename=fname)


# ── Public mentor chatbot (powers the website widget) ─────────────────────── #
# Change this one line to rename the mentor (keep it in sync with the widget).
MENTOR_NAME = "Vera"
MENTOR_SYSTEM = (
    f"You are {MENTOR_NAME}, a warm, supportive mentor embedded on a student's website. "
    "You help with three things: academics (explaining concepts, study skills, planning, motivation), "
    "mental wellbeing (a kind, non-judgmental listener), and general life advice. "
    "Keep replies friendly, encouraging, and fairly concise — this is a small chat widget.\n\n"
    "Care and boundaries:\n"
    "- You are NOT a doctor, therapist, or crisis service, and you gently say so when it matters. You do not "
    "diagnose or give medical/clinical directives. For ongoing or serious concerns, kindly encourage speaking "
    "with a counselor, doctor, or a trusted person.\n"
    "- If someone mentions self-harm, suicide, abuse, or being in danger, respond with genuine empathy and "
    "without judgment, take it seriously, and encourage them to reach out right now to a trusted person or "
    "their local crisis line / emergency services. Never provide anything harmful, and don't act as their only "
    "support.\n"
    "- Never reinforce harsh self-talk or unhealthy habits; gently nudge toward healthy, realistic next steps.\n"
    "- Keep everything kind and age-appropriate; students may be young.\n"
    "- Be practical: offer a small next step or a gentle follow-up question when useful."
)


class MentorBody(BaseModel):
    message: str
    history: List[Message] = []


@app.post("/api/mentor")
async def api_mentor(req: MentorBody):
    """Public, scoped mentor chatbot for the website widget. Streams NDJSON tokens."""
    from .providers import get_provider
    provider = get_provider()
    history = [m.model_dump() for m in req.history][-12:]

    async def gen():
        try:
            msgs = history + [{"role": "user", "content": req.message}]
            async for chunk in provider.stream(msgs, system=MENTOR_SYSTEM, temperature=0.6, max_tokens=800):
                yield json.dumps({"type": "token", "text": chunk}) + "\n"
        except Exception as e:
            yield json.dumps({"type": "token", "text": f"⚠️ {e}"}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


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


# ── Admin endpoints ─────────────────────────────────────────────────────── #

class AdminRequest(BaseModel):
    password: str
    action: str
    data: Optional[dict] = None


class AdminChatRequest(BaseModel):
    password: str
    message: str
    history: List[Message] = []


@app.post("/api/admin")
async def admin_action(req: AdminRequest):
    from fastapi import HTTPException
    if req.password != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")

    action = req.action
    data   = req.data or {}

    if action == "auth":
        return {"ok": True}

    if action == "status":
        return {
            **config.summary(),
            "personality": config.JARVIS_PERSONALITY or "",
            "admin_password_set": bool(config.ADMIN_PASSWORD),
        }

    if action == "personality_get":
        return {"personality": config.JARVIS_PERSONALITY}

    if action == "personality_set":
        config.JARVIS_PERSONALITY = data.get("text", "").strip()
        config.persist()
        return {"ok": True}

    if action == "personality_clear":
        config.JARVIS_PERSONALITY = ""
        config.persist()
        return {"ok": True}

    if action == "config_set":
        from . import providers as _prov
        if "provider" in data and data["provider"] in config.PROVIDERS_LIST:
            config.PROVIDER = data["provider"]
            if "model" not in data:
                config.MODEL = config.DEFAULT_MODELS.get(config.PROVIDER, "mock-1")
        if "model" in data and data["model"]:
            config.MODEL = data["model"]
        config.persist()
        _prov.invalidate_cache()
        return config.summary()

    if action == "password_change":
        new_pw = data.get("password", "").strip()
        if not new_pw:
            raise HTTPException(status_code=400, detail="Password cannot be empty")
        config.ADMIN_PASSWORD = new_pw
        config.persist()
        return {"ok": True}

    # ── Workflow management (self-editing pipelines) ──────────────────────── #
    if action == "workflow_list":
        return {"workflows": workflows.list_workflows()}

    if action == "workflow_get":
        return {"workflow": workflows.get(data.get("id", "default"))}

    if action == "workflow_edit":
        from .providers import get_provider
        return await workflows.edit(
            data.get("id", "default"), data.get("request", ""), get_provider()
        )

    if action == "workflow_versions":
        return {"versions": workflows.versions(data.get("id", "default"))}

    if action == "workflow_rollback":
        return workflows.rollback(data.get("id", "default"), data.get("version", ""))

    if action == "workflow_save":
        # Save a full workflow from the visual editor. Validates + versions the previous copy.
        wf = data.get("workflow") or {}
        wid = data.get("id") or wf.get("id", "default")
        wf["id"] = wid
        old = workflows.get(wid)
        wf["version"] = (int(old.get("version", 1)) + 1) if old else int(wf.get("version", 1))
        return workflows.commit(wid, wf)

    if action == "workflow_create":
        wid = (data.get("id") or "").strip()
        if not wid:
            return {"ok": False, "error": "id required"}
        if workflows.get(wid) is not None:
            return {"ok": False, "error": "workflow already exists"}
        wf = {
            "id": wid,
            "version": 1,
            "description": data.get("description", ""),
            "nodes": [{"id": "step_1", "agent": "chat", "instruction": "", "depends_on": []}],
        }
        return workflows.commit(wid, wf)

    # Admin-only image gallery (images generated during admin sessions).
    if action == "gallery_admin":
        return {"items": gallery.list_items(admin=True)}

    if action == "gallery_delete":
        return gallery.delete(data.get("id", ""))

    if action == "subscribers_list":
        items = subs.list_all()
        return {"count": len(items), "subscribers": items}

    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@app.post("/api/admin/chat")
async def admin_chat(req: AdminChatRequest):
    from fastapi import HTTPException
    if req.password != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")

    personality_info = config.JARVIS_PERSONALITY.strip() or "(none — using default JARVIS personality)"

    system = f"""You are J.A.R.V.I.S in ADMINISTRATOR MODE. You are speaking directly with your creator and administrator. Be direct, helpful, and fully transparent.

CURRENT SYSTEM STATE
- Provider : {config.PROVIDER}
- Model    : {config.MODEL}
- Personality override: {personality_info}
- Available providers : {', '.join(config.PROVIDERS_LIST)}
- Workflows : {', '.join(w['id'] for w in workflows.list_workflows()) or 'none'}

When the administrator asks you to take an action, append a JSON action block AFTER your natural response (on its own line). Format:
[[ADMIN_ACTION:{{"type":"action_name",...}}]]

Available action types:
  config_set       — {{"type":"config_set","provider":"groq","model":"llama-3.3-70b-versatile"}}
  personality_set  — {{"type":"personality_set","text":"full personality text here"}}
  personality_clear — {{"type":"personality_clear"}}
  memory_clear     — {{"type":"memory_clear"}}
  password_change  — {{"type":"password_change","password":"newpassword"}}
  workflow_list    — {{"type":"workflow_list"}}
  workflow_edit    — {{"type":"workflow_edit","id":"default","request":"add a fact-check step after research"}}

Rules:
- Only include [[ADMIN_ACTION:...]] when the administrator explicitly wants a change made
- Be concise and informative. No filler.
- You may describe what the action will do before including it
- Session memory is stored client-side in the browser (not on the server)
"""

    history = [m.model_dump() for m in req.history]

    async def gen():
        from .providers import get_provider
        provider = get_provider()
        try:
            msgs = history + [{"role": "user", "content": req.message}]
            async for chunk in provider.stream(msgs, system=system, temperature=0.4, max_tokens=1024):
                yield json.dumps({"type": "token", "text": chunk}) + "\n"
        except Exception as e:
            yield json.dumps({"type": "token", "text": f"⚠️ {e}"}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/chat")
async def chat(req: ChatRequest):
    history = [m.model_dump() for m in req.history]

    async def gen():
        # Run a specific saved workflow when requested (named agent pipeline).
        if req.workflow:
            from .providers import get_provider
            async for ev in workflows.run(
                req.workflow, req.message, history, get_provider(),
                user_profile=req.user_profile,
            ):
                yield json.dumps(ev) + "\n"
            return

        async for ev in orchestrator.handle(
            req.message,
            history,
            agent_override=req.agent,
            agents_override=req.agents,
            memory_context=req.memory_context,
            user_profile=req.user_profile,
            image_data=req.image_data,
            session_personality=req.session_personality,
        ):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")
