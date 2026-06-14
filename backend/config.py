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
    set_key(str(_ENV_PATH), "OLLAMA_HOST",    OLLAMA_HOST)
    set_key(str(_ENV_PATH), "OLLAMA_API_KEY", OLLAMA_API_KEY)
    set_key(str(_ENV_PATH), "GROQ_API_KEY",  GROQ_API_KEY)
    set_key(str(_ENV_PATH), "GEMINI_API_KEY",GEMINI_API_KEY)
    set_key(str(_ENV_PATH), "OPENAI_API_KEY",OPENAI_API_KEY)
    set_key(str(_ENV_PATH), "OPENAI_BASE_URL",OPENAI_BASE_URL)
