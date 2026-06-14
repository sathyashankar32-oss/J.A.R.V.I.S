# Agentic AI — trial edition

A small but complete agentic system with a Claude-style chat UI. One
**orchestrator** reads your prompt, decides which **specialized agent** should
handle it, and streams the result back. Everything runs on **free models** —
either fully local (Ollama) or a free API tier (Groq / Gemini). A built-in
**mock** mode lets you see the whole thing working with zero setup.

```
You ──▶ Orchestrator ──routes to──▶  Coding · Writing · Solver · Research · Image
                │
                └── routing + agent choice are shown in the UI with every reply
```

## What's inside

| Agent | What it does |
|-------|--------------|
| **Coding** | Writes and explains code (does not auto-run it) |
| **Writing** | Drafts and edits prose |
| **Solver** | Step-by-step math / logic / analysis |
| **Research** | **Deep research**: splits a question into sub-questions, searches the web (DuckDuckGo), reads the top pages, and writes a **cited** synthesis |
| **Image** | Generates an image from a prompt (Pollinations, free) |

Adding an agent is one line in `backend/orchestrator.py` — the UI and router
pick it up automatically.

## Run it

You need **Python 3.10+**. Open this folder in VS Code, then in a terminal:

```bash
# macOS / Linux
./run.sh
```
```bash
# Windows / manual
python -m venv .venv
.venv\Scripts\activate           # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app:app --reload --port 8000
```

Open **http://localhost:8000**. With no configuration it starts in **mock
mode** — the UI, routing, research trace, and source list are all real; only
the model's words are placeholder text. Use this to confirm everything works,
then switch to a real model below.

## Use a real (free) model

Copy `.env.example` to `.env` and pick one:

**Option A — Ollama (fully free, local, no key, best for privacy)**
1. Install from <https://ollama.com>
2. `ollama pull llama3.2`
3. In `.env`: `PROVIDER=ollama`
*(Research and images need internet even with a local model.)*

**Option B — Groq (free tier, very fast)**
1. Get a key at <https://console.groq.com/keys>
2. In `.env`: `PROVIDER=groq` and `GROQ_API_KEY=...`

**Option C — Google Gemini (free tier)**
1. Get a key at <https://aistudio.google.com/apikey>
2. In `.env`: `PROVIDER=gemini` and `GEMINI_API_KEY=...`

Restart the server after editing `.env`.

## Try these (to see each capability)

- **Coding:** *"Write a Python function that finds the longest palindromic substring."*
- **Deep research:** *"Research the current state of solid-state batteries and cite sources."* — watch the trace expand with sub-questions and sources.
- **Solver:** *"A train goes 60 km at 30 km/h then 60 km at 60 km/h. Average speed?"*
- **Writing:** *"Draft a warm 4-sentence thank-you email to a mentor."*
- **Image:** *"Generate an image of a lighthouse on a cliff at sunset, watercolor style."*

The pill above each reply shows which agent the orchestrator chose.

## Project layout

```
backend/
  app.py            FastAPI app: serves UI, streams responses (NDJSON)
  config.py         settings from .env
  providers.py      model backends: mock | ollama | groq | gemini | openai
  orchestrator.py   agent registry + intent routing + dispatch
  agents/           coding, writing, solver, research, image
  tools/web.py      free web search + page extraction
frontend/
  index.html        the Claude-style chat UI (no build step)
```

## Notes

- **Basic models route imperfectly.** The router asks the model for a JSON
  choice and falls back to a keyword heuristic, so even small models behave
  sensibly. Bigger/free-tier models route and answer noticeably better.
- The coding agent **generates** code and does not execute it (running
  model-generated code automatically is unsafe). Wiring a sandboxed runner is a
  natural next step.
- Research quality scales with the model: a 70B Groq model writes a much better
  synthesis than a small local one, but the pipeline is identical.
