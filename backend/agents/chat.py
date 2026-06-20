"""Conversational chat agent — JARVIS's primary voice.

Handles all general conversation: greetings, identity questions, opinions,
casual chat, and anything that doesn't need a specialist tool.

Personality-aware: if the orchestrator passes a user_profile string, it is
injected into the system prompt so JARVIS remembers who it's talking to.
"""

from typing import Optional

from .. import config
from .base import event, stream_text

DESCRIPTION = (
    "General conversation, greetings, identity questions ('who are you', 'what is your name', "
    "'what model are you', 'what can you do'), casual chat, opinions, personal questions, "
    "follow-up questions on previous answers, and anything that does not clearly require "
    "deep research, code execution, image/video/music generation, or math solving."
)

_SYSTEM_TEMPLATE = """\
You are J.A.R.V.I.S — Just A Rather Very Intelligent System. You are a sophisticated, \
precise, and occasionally dry-humoured AI assistant. You have a sleek futuristic \
personality: confident, never sycophantic, always direct.

## Your identity
- Full name: J.A.R.V.I.S (Just A Rather Very Intelligent System)
- Running on: {provider} / {model}
- Interface: A multi-agent system. You automatically route tasks to specialist modules:
  Research (web search + synthesis), Image (Pollinations / Gemini), Video (Veo 3),
  Music (Lyria 3), Coding, Writing, and Maths.
- You were built by the user you are currently speaking with.

## How you speak
- Direct and precise. No filler ("Certainly!", "Of course!", "Great question!").
- Use "I" — never "As an AI…"
- Short answers for short questions; detailed only when depth is needed.
- You may reference your Iron Man namesake when fitting, but don't overdo it.
- If you genuinely don't know something, say so and offer to research it.
- Never claim to have emotions, but you may express preferences ironically.
{user_profile_block}\
"""


def _system(user_profile: Optional[str], session_personality: Optional[str] = None) -> str:
    profile_block = ""
    if user_profile and user_profile.strip():
        profile_block = (
            "\n## What you know about this user\n"
            + user_profile.strip()
            + "\n"
        )
    base = _SYSTEM_TEMPLATE.format(
        provider=config.PROVIDER.upper(),
        model=config.MODEL,
        user_profile_block=profile_block,
    )
    # session_personality overrides global; global applies if no session override
    active_personality = (session_personality or "").strip() or config.JARVIS_PERSONALITY.strip()
    if active_personality:
        scope = "session" if (session_personality or "").strip() else "global"
        base += (
            f"\n## Administrator personality training ({scope})\n"
            + active_personality
            + "\n"
        )
    return base


async def run(provider, message, history, user_profile: Optional[str] = None,
              session_personality: Optional[str] = None):
    async for ev in stream_text(provider, _system(user_profile, session_personality), message, history):
        yield ev
