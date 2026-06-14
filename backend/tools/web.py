"""Free web tools for the research agent.

- search(): DuckDuckGo via the `ddgs` package (no API key required).
- fetch_extract(): pulls a page and extracts the main readable text.

Both are synchronous; the research agent calls them inside asyncio.to_thread.
Everything degrades gracefully: if a dependency or the network is unavailable,
functions return empty results instead of raising, so the app never crashes.
"""

from dataclasses import dataclass
from typing import List

import httpx

# DuckDuckGo client. The package was renamed ddgs -> try both.
try:
    from ddgs import DDGS  # type: ignore
except Exception:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except Exception:
        DDGS = None

try:
    import trafilatura  # type: ignore
except Exception:  # pragma: no cover
    trafilatura = None


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def search(query: str, k: int = 4) -> List[SearchResult]:
    if DDGS is None:
        return []
    out: List[SearchResult] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=k):
                out.append(
                    SearchResult(
                        title=r.get("title") or r.get("href", "result"),
                        url=r.get("href") or r.get("url", ""),
                        snippet=r.get("body", ""),
                    )
                )
    except Exception:
        return out
    return out


def fetch_extract(url: str, max_chars: int = 2400) -> str:
    """Fetch a URL and return its main text content, truncated."""
    if not url:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AgenticAITrial/1.0)"}
        resp = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return ""

    text = ""
    if trafilatura is not None:
        try:
            text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
        except Exception:
            text = ""
    if not text:
        text = _crude_strip(html)
    return text.strip()[:max_chars]


def _crude_strip(html: str) -> str:
    import re

    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text
