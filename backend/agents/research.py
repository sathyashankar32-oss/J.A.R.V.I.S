"""Deep research agent.

Pipeline:
  1. Decompose the question into a few focused sub-questions.
  2. Web-search each sub-question (DuckDuckGo, free).
  3. Fetch and extract the main text of the top unique results.
  4. Synthesize a structured, source-cited answer from what was gathered.

Each stage streams a "step" event so the UI shows the research happening.
"""

import asyncio
import json
import re

from .. import config
from ..providers import gemini_provider
from ..tools import web
from .base import event

DESCRIPTION = "Performs deep, multi-step web research: breaks a question into sub-questions, searches the web, reads sources, and writes a cited synthesis. Use for current information, comparisons, investigations, and 'find out / what's the latest' questions."

_PLAN_SYSTEM = (
    "You are a research planner. Given a question, produce a JSON array of "
    f"{config.RESEARCH_MAX_SUBQUESTIONS} or fewer focused web-search queries that together "
    "would answer it well. Return ONLY the JSON array of short query strings, nothing else."
)

_SYNTH_SYSTEM = (
    "You are a research analyst. Using ONLY the numbered sources provided, write a clear, "
    "well-structured answer to the user's question. Cite sources inline like [1], [2] right "
    "where you use them. Be accurate and note disagreements between sources. If the sources do "
    "not cover something, say so rather than inventing it. Do not list the sources at the end — "
    "they are shown separately."
)


async def run(provider, message, history):
    # Research always uses Gemini regardless of the main provider setting
    if config.GEMINI_API_KEY:
        provider = gemini_provider()
        yield event("step", text="Planning the research · powered by Gemini")
    else:
        yield event("step", text="Planning the research")
    subqs = await _plan(provider, message)
    yield event("step", text=f"Investigating {len(subqs)} angle(s)", detail=subqs)

    # 2 + 3. Search and read ---------------------------------------------
    sources = []
    seen = set()
    for q in subqs:
        yield event("step", text=f"Searching: {q}")
        results = await asyncio.to_thread(web.search, q, config.RESEARCH_RESULTS_PER_QUERY)
        if not results:
            yield event("step", text=f"No results (search may be offline): {q}")
        for r in results:
            if not r.url or r.url in seen:
                continue
            seen.add(r.url)
            text = await asyncio.to_thread(web.fetch_extract, r.url)
            if text and len(text) > 200:
                sources.append({"title": r.title, "url": r.url, "text": text})
                yield event("step", text=f"Read: {r.title[:70]}")
            if len(sources) >= config.RESEARCH_MAX_SOURCES:
                break
        if len(sources) >= config.RESEARCH_MAX_SOURCES:
            break

    if not sources:
        yield event(
            "token",
            text=(
                "I couldn't gather any web sources. This usually means the machine has no "
                "internet access, or the `ddgs` package isn't installed. With a real provider "
                "and a network connection, this agent searches the web, reads the top pages, and "
                "writes a cited synthesis here."
            ),
        )
        return

    yield event("sources", items=[{"title": s["title"], "url": s["url"]} for s in sources])
    yield event("step", text=f"Synthesizing from {len(sources)} source(s)")

    # 4. Synthesize -------------------------------------------------------
    context = "\n\n".join(
        f"[{i+1}] {s['title']} — {s['url']}\n{s['text']}" for i, s in enumerate(sources)
    )
    prompt = f"Question: {message}\n\nSources:\n{context}\n\nWrite the cited answer now."
    async for chunk in provider.stream(
        [{"role": "user", "content": prompt}], system=_SYNTH_SYSTEM, temperature=0.4, max_tokens=1600
    ):
        yield event("token", text=chunk)


async def _plan(provider, message):
    """Ask the model for sub-questions; fall back to the raw question."""
    try:
        raw = await provider.complete(
            [{"role": "user", "content": f"Question: {message}"}],
            system=_PLAN_SYSTEM,
            temperature=0.3,
            max_tokens=300,
        )
        match = re.search(r"\[.*\]", raw, re.S)
        if match:
            arr = json.loads(match.group(0))
            qs = [str(x).strip() for x in arr if str(x).strip()]
            if qs:
                return qs[: config.RESEARCH_MAX_SUBQUESTIONS]
    except Exception:
        pass
    return [message]
