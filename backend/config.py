"""Central configuration. Everything is read from environment variables
(or a .env file). Sensible free defaults are baked in so the app runs with
zero setup using the built-in `mock` provider.

Changes made at runtime (via /api/config) are written back to .env so they
survive server restarts.
"""

import os
from pathlib import Path
from dotenv import load_dotenv, set_key

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

# Which model backend to use: mock | ollama | groq | gemini | openai
PROVIDER = os.getenv("PROVIDER", "ollama").strip() or "ollama"

# Model name. The right default depends on the provider.
DEFAULT_MODELS = {
    "mock": "mock-1",
    "ollama": "gemma3:4b",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
}
_DEFAULT_MODELS = DEFAULT_MODELS  # backwards compat
PROVIDERS_LIST = list(DEFAULT_MODELS.keys())

MODEL = os.getenv("MODEL", "").strip() or DEFAULT_MODELS.get(PROVIDER, "mock-1")

# --- Provider-specific settings ---
OLLAMA_HOST    = os.getenv("OLLAMA_HOST",    "http://localhost:11434").rstrip("/")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()

# Ollama generation limits — set very high so big outputs (huge code, long docs)
# are not truncated. num_predict = max tokens to generate; num_ctx = context window.
# Bound by what the chosen model actually supports. Raise further in .env if needed.
OLLAMA_MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "32768"))
OLLAMA_NUM_CTX    = int(os.getenv("OLLAMA_NUM_CTX",    "32768"))

# Admin mode
ADMIN_PASSWORD     = os.getenv("ADMIN_PASSWORD", "jarvis").strip()
JARVIS_PERSONALITY = os.getenv("JARVIS_PERSONALITY", "").strip()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Generic OpenAI-compatible endpoint (works for many free/self-hosted gateways)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# --- Research tool settings ---
RESEARCH_MAX_SUBQUESTIONS = int(os.getenv("RESEARCH_MAX_SUBQUESTIONS", "4"))
RESEARCH_RESULTS_PER_QUERY = int(os.getenv("RESEARCH_RESULTS_PER_QUERY", "3"))
RESEARCH_MAX_SOURCES = int(os.getenv("RESEARCH_MAX_SOURCES", "8"))

# --- Image tool settings (Pollinations is free and needs no key) ---
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://image.pollinations.ai/prompt").rstrip("/")

# --- Coding agent workspace (the only folder the agent may read/write) ---
# Point this at the project you want JARVIS to edit. Defaults to a sandbox
# folder inside the app so nothing outside it can be touched by accident.
AGENT_WORKSPACE = os.getenv("AGENT_WORKSPACE", "").strip() or str(
    Path(__file__).resolve().parent.parent / "workspace"
)

# --- Canva Connect API (optional; needed only for the Canva agent) ---
CANVA_API_TOKEN = os.getenv("CANVA_API_TOKEN", "").strip()
CANVA_API_BASE  = os.getenv("CANVA_API_BASE", "https://api.canva.com/rest").rstrip("/")


def summary() -> dict:
    """Safe-to-expose snapshot of the active configuration (no secrets)."""
    return {
        "provider": PROVIDER,
        "model": MODEL,
        "free": PROVIDER in ("mock", "ollama"),
        "providers": PROVIDERS_LIST,
        "default_models": DEFAULT_MODELS,
        "needs_key": PROVIDER in ("groq", "gemini", "openai"),
        "ollama_host":    OLLAMA_HOST    if PROVIDER == "ollama" else None,
        "ollama_has_key": bool(OLLAMA_API_KEY) if PROVIDER == "ollama" else None,
        "openai_base_url": OPENAI_BASE_URL if PROVIDER == "openai" else None,
    }


def persist():
    """Write the current in-memory config back to .env so it survives restarts."""
    _ENV_PATH.touch(exist_ok=True)
    set_key(str(_ENV_PATH), "PROVIDER",      PROVIDER)
    set_key(str(_ENV_PATH), "MODEL",         MODEL)
    set_key(str(_ENV_PATH), "OLLAMA_HOST",         OLLAMA_HOST)
    set_key(str(_ENV_PATH), "OLLAMA_API_KEY",      OLLAMA_API_KEY)
    set_key(str(_ENV_PATH), "ADMIN_PASSWORD",      ADMIN_PASSWORD)
    set_key(str(_ENV_PATH), "JARVIS_PERSONALITY",  JARVIS_PERSONALITY)
    set_key(str(_ENV_PATH), "GROQ_API_KEY",  GROQ_API_KEY)
    set_key(str(_ENV_PATH), "GEMINI_API_KEY",GEMINI_API_KEY)
    set_key(str(_ENV_PATH), "OPENAI_API_KEY",OPENAI_API_KEY)
    set_key(str(_ENV_PATH), "OPENAI_BASE_URL",OPENAI_BASE_URL)
    set_key(str(_ENV_PATH), "AGENT_WORKSPACE", AGENT_WORKSPACE)
    set_key(str(_ENV_PATH), "CANVA_API_TOKEN", CANVA_API_TOKEN)