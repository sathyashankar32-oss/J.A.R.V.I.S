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
| **Coding** | Writes, explains, and **edits real files** in your workspace — reads what it needs, writes changes back to disk, and shows a diff |
| **Writing** | Drafts and edits prose |
| **Solver** | Step-by-step math / logic / analysis |
| **Research** | **Deep research**: splits a question into sub-questions, searches the web (DuckDuckGo), reads the top pages, and writes a **cited** synthesis |
| **Image** | Generates an image from a prompt (Pollinations, free) |
| **Canva** | Creates a design in your Canva account + a PNG export (needs a Canva Connect token) |

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
  workflows.py      saved, self-editing agent pipelines (+ versioning)
  agents/           coding, writing, solver, research, image
  tools/web.py      free web search + page extraction
config/
  workflows/*.json  named workflow definitions (editable at runtime)
  workflow.schema.json   schema used to validate every edit
frontend/
  index.html        the Claude-style chat UI (no build step)
```

## Workflows (self-editing pipelines)

A **workflow** is a named, saved multi-step agent pipeline stored as plain JSON
in `config/workflows/`. Each node names one of the real agents (`research`,
`writing`, `coding`, `solver`, `image`, `image_edit`, `chat`), gives it a step
instruction, and lists which earlier steps feed it context. Because a workflow
is *data*, the assistant can rewrite its own workflows on command.

Two ship by default: **`default`** (research → draft → review) and
**`build_feature`** (research → implement).

**Run one** from the chat box:

```
//wf default write a 5-page brief on solid-state batteries
```

or via the API: `POST /api/chat` with `{"message": "...", "workflow": "default"}`.

**Edit one on command** (admin only — the system rewrites the workflow JSON for
you). Either use an explicit command:

```
//admin wf edit default add a fact-check step after research
```

or just say it in **admin chat** (`//admin chat`): *"add a proofread pass to the
default workflow"*. Behind the scenes it runs **propose → validate → version →
commit**: the model returns the full updated JSON, it's validated against
`workflow.schema.json` (agent names must be real, dependencies must resolve), the
previous version is saved to `config/workflows/versions/`, then the new one is
written. Every edit is therefore reversible.

**Inspect / undo:**

```
//admin wf                       list workflows
//admin wf get default           show the JSON
//admin wf versions default      list saved versions
//admin wf rollback default <file>   restore a previous version
```

**Token / cost control.** Any node may carry an optional `"model"` field. Put the
trivial steps (research, review) on a small fast model and reserve the large
model for the heavy step — the engine swaps models per step automatically.

**API endpoints:** `GET /api/workflows`, `GET /api/workflows/{id}`, and admin
actions `workflow_list` / `workflow_get` / `workflow_edit` / `workflow_versions`
/ `workflow_rollback` on `POST /api/admin`.

> Persona/personality is **not** in `config/persona.json` (that file is unused) —
> it lives in `config.JARVIS_PERSONALITY`, managed by the existing
> `//admin personality ...` commands. Also add `config/workflows/versions/` to
> `.gitignore` so version backups aren't committed.

## File editing & Canva

**The coding agent edits real files.** Point it at the project you want it to work on
by setting `AGENT_WORKSPACE` in `.env` to that folder's path (leave it blank to use a
safe `workspace/` sandbox inside the app). When you ask it to build or change code, it
reads the files it needs, writes the changes back to disk, and shows a unified diff of
everything it touched. It can only read/write inside that one folder — any path that
tries to escape it is rejected.

> Heads-up: it **overwrites** files in `AGENT_WORKSPACE`. Point it at a folder under
> git so you can always review and undo its changes.

**Canva.** The `canva` agent creates a real design in your Canva account via the Canva
Connect API and returns an editable link plus a PNG. Set it up once:

1. At **canva.dev**, create a **Connect API** integration.
2. Grant the scopes `design:content:write`, `design:meta:read`, `design:content:read`.
3. Complete Canva's OAuth flow to obtain an **access token**.
4. Put it in `.env` as `CANVA_API_TOKEN=...` (or have an admin set it) and restart.

Without a token the agent replies with these steps instead of erroring. Note: the Canva
API creates/opens and exports designs — it is **not** a free-form text-to-image
generator. For instant AI images with zero setup, the free `image` agent already works.

## Notes

- **Basic models route imperfectly.** The router asks the model for a JSON
  choice and falls back to a keyword heuristic, so even small models behave
  sensibly. Bigger/free-tier models route and answer noticeably better.
- The coding agent **generates** code and does not execute it (running
  model-generated code automatically is unsafe). Wiring a sandboxed runner is a
  natural next step.
- Research quality scales with the model: a 70B Groq model writes a much better
  synthesis than a small local one, but the pipeline is identical.
