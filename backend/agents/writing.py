"""Writing agent — drafts and humanises prose to defeat AI detectors.

Strategy: two-stage approach.
  1. Draft the content with the active provider.
  2. Extract bullet-point key ideas, then have Gemini write a FRESH version
     from scratch as a human — not a "rewrite" but a completely new composition
     from the same ideas. This avoids the rewriting-AI-as-AI trap.
  3. Apply a programmatic post-processor to inject statistical burstiness.
"""

import random
import re

from .. import config
from ..providers import gemini_provider
from .base import event, stream_text

DESCRIPTION = (
    "Drafts and edits prose: essays, emails, posts, stories, summaries, rewrites, "
    "and general writing. Output is humanised to pass AI detectors."
)

_WRITE_SYSTEM = (
    "You are a skilled writing agent. Produce clear, well-organised prose. "
    "Match the tone and format the user requests."
)

_EXTRACT_SYSTEM = (
    "Extract the key ideas, facts, arguments, and structure from the text below. "
    "Return a concise bullet-point list of every point that must appear in the final piece. "
    "Include tone, audience, and format notes. Be thorough — nothing should be lost."
)

_HUMAN_WRITE_SYSTEM = """\
You are a human writer — a real person — sitting down to write this piece from your own notes.
You have a list of points you need to cover. Write naturally, the way you actually think and talk.

Your writing style:
- You write how you think. Short sentences when making a point. Longer ones when you're building up to something or explaining a connection between ideas.
- You use contractions constantly. It's, don't, you'll, can't, won't, I've, we're.
- You start sentences with And, But, So, Yet sometimes — because that's how people actually talk.
- You use em dashes — like this — when a thought needs a pause or an aside.
- You throw in parentheses for side thoughts (the kind you'd say quietly in conversation).
- You vary your paragraphs wildly. Some are one sentence. Some are four or five. No pattern.
- You hedge naturally: "I think", "probably", "honestly", "in most cases", "to be fair".
- You're not afraid to say "look" or "here's the thing" or "the truth is" to signal a key point.
- You occasionally use a one-word or two-word sentence. For effect.
- You avoid: "delve", "crucial", "straightforward", "moreover", "furthermore", "it is worth noting",
  "in conclusion", "in today's world", "a testament to", "multifaceted", "robust", "utilize",
  "facilitate", "leverage" (as verb), "paradigm", "nuanced", "commendable", "notably",
  "significantly", "undoubtedly", "subsequently", "it is important to", "one must consider".
- You're not robotic. You show mild personality, occasional dry humour, and genuine opinion.
- Every now and then you acknowledge that something is actually complicated, rather than glossing over it.

STRICT: cover every point from the notes. Don't add new facts. Don't write a meta-commentary.
Output ONLY the written piece, nothing else. Preserve any requested format (headers, bullets, etc).
"""


def _bust_sentences(text: str) -> str:
    """
    Programmatic post-processor: injects burstiness by splitting some long sentences
    and merging some short ones — the key metric AI detectors measure.
    """
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    i = 0
    while i < len(sentences):
        s = sentences[i]
        word_count = len(s.split())

        # Randomly split very long sentences at a conjunction
        if word_count > 25 and random.random() < 0.5:
            for conj in [', and ', ', but ', ', so ', ', which ', ', while ']:
                if conj in s:
                    parts = s.split(conj, 1)
                    result.append(parts[0] + '.')
                    result.append(parts[1].capitalize())
                    break
            else:
                result.append(s)

        # Randomly merge two adjacent short sentences
        elif word_count < 8 and i + 1 < len(sentences) and random.random() < 0.4:
            next_s = sentences[i + 1]
            if len(next_s.split()) < 8:
                merged = s.rstrip('.!?') + ', and ' + next_s[0].lower() + next_s[1:]
                result.append(merged)
                i += 1  # skip next sentence since we merged it
            else:
                result.append(s)
        else:
            result.append(s)
        i += 1

    return ' '.join(result)


async def run(provider, message, history):
    # Stage 1: Draft
    yield event("step", text="Generating draft")
    draft_chunks = []
    async for ev in stream_text(provider, _WRITE_SYSTEM, message, history, temperature=0.85):
        if ev.get("type") == "token":
            draft_chunks.append(ev["text"])

    draft = "".join(draft_chunks).strip()
    if not draft:
        yield event("token", text="⚠ Writing agent produced no output.")
        return

    if not config.GEMINI_API_KEY:
        # No Gemini — just stream the draft as-is
        yield event("step", text="No Gemini key — skipping humaniser")
        for chunk in [draft[i:i+50] for i in range(0, len(draft), 50)]:
            yield event("token", text=chunk)
        return

    gem = gemini_provider()

    # Stage 2: Extract key points from the draft
    yield event("step", text="Extracting key ideas")
    try:
        notes = await gem.complete(
            [{"role": "user", "content": draft}],
            system=_EXTRACT_SYSTEM,
            temperature=0.2,
            max_tokens=1024,
        )
    except Exception as e:
        yield event("step", text=f"Extraction failed ({e}) — using draft directly")
        notes = draft

    # Stage 3: Write fresh from notes as a human
    yield event("step", text="Recomposing as human writer")
    try:
        human_text = await gem.complete(
            [{"role": "user", "content": f"Write this piece using these notes:\n\n{notes}"}],
            system=_HUMAN_WRITE_SYSTEM,
            temperature=0.97,   # high variance = unpredictable word choices = low AI score
            max_tokens=4096,
        )
        human_text = human_text.strip()
    except Exception as e:
        yield event("step", text=f"Human rewrite failed ({e}) — using draft")
        human_text = draft

    # Stage 4: Programmatic burstiness injection
    yield event("step", text="✓ Humanised — ready")
    try:
        final = _bust_sentences(human_text)
    except Exception:
        final = human_text

    # Stream the final result
    words = final.split(" ")
    chunk = ""
    for i, word in enumerate(words):
        chunk += word + " "
        if len(chunk) >= 50 or i == len(words) - 1:
            yield event("token", text=chunk)
            chunk = ""
