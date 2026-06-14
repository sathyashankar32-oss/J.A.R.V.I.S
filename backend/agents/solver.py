"""Solver agent — step-by-step reasoning for math, logic, and analysis."""

from .base import stream_text

DESCRIPTION = "Solves math, logic, and analytical problems with step-by-step reasoning. Use for calculations, proofs, puzzles, and structured problem-solving."

SYSTEM = (
    "You are a careful reasoning agent. Solve the problem step by step, showing the "
    "key intermediate steps clearly and concisely. State any formula you use. End with "
    "a clearly marked final answer. Do not skip steps that affect the result, but do not pad."
)


async def run(provider, message, history):
    async for ev in stream_text(provider, SYSTEM, message, history, temperature=0.2):
        yield ev
