"""Helpers shared by the simple agents.

Agents are async generators that yield plain dict "events". The orchestrator
forwards these to the browser as newline-delimited JSON. Event types:
    {"type": "token",  "text": "..."}        a chunk of the answer
    {"type": "step",   "text": "...", "detail": [...]}  research progress
    {"type": "sources","items": [{title,url}]}          cited sources
    {"type": "image",  "url": "...", "alt": "..."}       a generated image
The orchestrator emits {"type":"route",...} first and {"type":"done"} last.
"""


def event(kind: str, **data):
    return {"type": kind, **data}


async def stream_text(provider, system, message, history, temperature=0.7):
    """Run a plain single-model agent, yielding token events."""
    msgs = list(history) + [{"role": "user", "content": message}]
    async for chunk in provider.stream(msgs, system=system, temperature=temperature):
        yield event("token", text=chunk)
