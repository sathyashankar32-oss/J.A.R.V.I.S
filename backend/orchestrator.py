"""The orchestrator.

It owns the agent registry, decides which agent(s) should handle a request
(intent routing), and dispatches to them. When multiple agents are needed,
they run as a sequential pipeline — each agent receives the original message
plus the accumulated output of all prior agents as context.

Single-agent path: exactly as before (zero overhead).
Multi-agent path: agent 1 runs → output fed as context to agent 2, etc.
"""

import asyncio
import json
import re
import inspect

from . import config
from .agents import chat, coding, image, image_edit, research, solver, writing

# ---- Agent registry: name -> {description, run} --------------------------- #
AGENTS = {
    "chat":       {"description": chat.DESCRIPTION,       "run": chat.run},
    "coding":     {"description": coding.DESCRIPTION,     "run": coding.run},
    "writing":    {"description": writing.DESCRIPTION,    "run": writing.run},
    "solver":     {"description": solver.DESCRIPTION,     "run": solver.run},
    "research":   {"description": research.DESCRIPTION,   "run": research.run},
    "image":      {"description": image.DESCRIPTION,      "run": image.run},
    "image_edit": {"description": image_edit.DESCRIPTION, "run": image_edit.run},
}
DEFAULT_AGENT = "chat"


def registry():
    return [{"name": n, "description": a["description"]} for n, a in AGENTS.items()]


# ---- Routing -------------------------------------------------------------- #
_KEYWORDS = {
    "chat": [
        "who are you", "what are you", "your name", "what is your name",
        "what model", "which model", "what can you do", "your capabilities",
        "are you an", "do you have", "can you feel", "how are you",
        "hello", "hi jarvis", "hey jarvis", "good morning", "good evening",
        "tell me about yourself", "introduce yourself",
        "what do you think about", "your opinion", "do you like",
        "thank you", "thanks", "nice work", "well done",
    ],
    "coding": [
        "code", "function", "bug", "debug", "python", "javascript", "java ", "c++",
        "api", "script", "regex", "sql", "algorithm", "compile", "class ", "refactor",
        "stack trace", "error:", "program", "html", "css", "react",
        "fix this code", "write a function", "implement", "develop", "build",
    ],
    "research": [
        "research", "find out", "latest news", "compare and contrast",
        "cite", "sources", "investigate", "deep dive", "market analysis",
        "trends in", "study shows", "according to", "statistics",
        "2024", "2025", "2026", "recent developments",
    ],
    "image": [
        "image", "picture", "draw", "photo", "illustration", "logo", "render",
        "generate an image", "create an image", "make an image",
        "art", "wallpaper", "poster", "icon", "visualise", "visualize",
    ],
    "solver": [
        "solve", "calculate", "compute", "math", "prove", "equation", "logic",
        "puzzle", "derivative", "integral", "probability", "step by step", "how many",
        "what is the value", "simplify", "factorise", "factorize",
    ],
    "writing": [
        "write me a", "draft ", "write an email", "essay", "blog post",
        "write a story", "rewrite", "proofread", "write a letter",
        "write a report", "write a poem",
    ],
}


def _heuristic(message: str) -> str:
    text = message.lower()
    scores = {name: 0 for name in AGENTS}
    for name, words in _KEYWORDS.items():
        for w in words:
            if w in text:
                scores[name] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else DEFAULT_AGENT


def _multi_route_system() -> str:
    lines = [f'- "{n}": {a["description"]}' for n, a in AGENTS.items()]
    return (
        "You are an intent router for a multi-agent AI system. Determine which agents "
        "are needed to FULLY answer the user's message. Use MORE than one agent only "
        "when the task genuinely spans multiple capabilities (e.g. research + coding, "
        "writing + image). Order them logically (e.g. research before coding so findings "
        "inform the code). Use exactly ONE agent if one is sufficient.\n\n"
        "Available agents:\n" + "\n".join(lines) +
        '\n\nReply with ONLY a JSON object: {"agents": ["name1", "name2"]}. '
        "Maximum 3 agents. No other text."
    )


async def multi_route(provider, message, history) -> list:
    """Returns an ordered list of agents to run as a pipeline."""
    if config.PROVIDER == "mock":
        return [_heuristic(message)]
    try:
        raw = await asyncio.wait_for(
            provider.complete(
                [{"role": "user", "content": message}],
                system=_multi_route_system(),
                temperature=0.0,
                max_tokens=60,
            ),
            timeout=15.0,
        )
        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            chosen = json.loads(match.group(0)).get("agents", [])
            valid = [a for a in chosen if a in AGENTS][:3]
            if valid:
                return valid
    except Exception:
        pass
    return [_heuristic(message)]


# ---- Dispatch ------------------------------------------------------------- #
async def handle(message, history, agent_override=None, agents_override=None,
                 memory_context=None, user_profile=None, image_data=None):
    """Yield the full stream of events for one user message.

    agents_override: list of agent names — runs them as a sequential pipeline.
    agent_override:  single agent name — pins to one agent (legacy/single override).
    """
    from .providers import get_provider
    provider = get_provider()

    # Prepend cross-session memory as a synthetic exchange
    effective_history = list(history)
    if memory_context and memory_context.strip():
        effective_history = [
            {"role": "user",      "content": f"[JARVIS MEMORY — previous sessions]\n{memory_context}"},
            {"role": "assistant", "content": "Memory context loaded. I have awareness of our prior conversations."},
        ] + effective_history

    # ── Determine the pipeline (ordered list of agent names) ──────────────── #
    if image_data:
        # Image attached → always image editor, ignores all overrides
        agent_names = ["image_edit"]
    elif agents_override and isinstance(agents_override, list):
        agent_names = [a for a in agents_override if a in AGENTS]
        if not agent_names:
            agent_names = [DEFAULT_AGENT]
    elif agent_override and agent_override in AGENTS:
        agent_names = [agent_override]
    else:
        agent_names = await multi_route(provider, message, effective_history)

    # ── Announce routing ──────────────────────────────────────────────────── #
    yield {"type": "route", "agent": agent_names[0], "agents": agent_names}

    # ── Run the pipeline ──────────────────────────────────────────────────── #
    context_parts = []   # accumulated text from each prior agent

    for idx, agent_name in enumerate(agent_names):

        # Section header when running more than one agent
        if len(agent_names) > 1:
            yield {
                "type":  "agent_section",
                "agent": agent_name,
                "index": idx,
                "total": len(agent_names),
            }

        # Build the effective message for this agent.
        # Agents after the first receive prior agents' output as context.
        if context_parts and idx > 0:
            prior_context = "\n\n".join(context_parts)
            effective_message = (
                f"{message}\n\n"
                f"[Pipeline context — output from the "
                f"{', '.join(agent_names[:idx])} agent(s) that ran before you. "
                f"Use this as input or reference for your task.]\n\n"
                f"{prior_context}"
            )
        else:
            effective_message = message

        try:
            agent_obj = AGENTS.get(agent_name, AGENTS[DEFAULT_AGENT])
            run_fn    = agent_obj["run"]
            sig       = inspect.signature(run_fn).parameters
            kwargs    = {}
            if "user_profile" in sig: kwargs["user_profile"] = user_profile
            if "image_data"   in sig: kwargs["image_data"]   = image_data

            collected = []
            async for ev in run_fn(provider, effective_message, effective_history, **kwargs):
                yield ev
                if ev.get("type") == "token":
                    collected.append(ev.get("text", ""))

            if collected:
                context_parts.append(
                    f"[{agent_name.upper()} AGENT]\n" + "".join(collected)
                )

        except Exception as e:
            yield {"type": "token", "text": f"\n\n⚠️ The {agent_name} agent hit an error: {e}"}

    yield {"type": "done"}
