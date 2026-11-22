"""Web_Search_Tool: Tavily first; DuckDuckGo when there is no Tavily key or Tavily fails."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.tools import BaseTool, tool

from aura.audit import AuditLog

FALLBACK_NOTICE = "I'm using my fallback search mode to find that for you."
MAX_RESULTS = 5
SNIPPET_CHARS = 600
TOTAL_CHARS = 4_000  # ~1k tokens
CITE = "Cite the URL of each result you use. If these results don't answer the question, say so."


@dataclass(frozen=True)
class Hit:
    title: str
    url: str
    content: str


Backend = Callable[[str, int], list[Hit]]


@dataclass(frozen=True)
class SearchResults:
    text: str
    backend: str  # "tavily" | "duckduckgo" | "none"
    fallback: bool  # True when DuckDuckGo produced the results


def tavily_backend(api_key: str) -> Backend:
    from tavily import TavilyClient

    client = TavilyClient(api_key=api_key)

    def search(query: str, n: int) -> list[Hit]:
        resp = client.search(query, max_results=n, search_depth="basic", timeout=15)
        return [Hit(r.get("title", ""), r.get("url", ""), r.get("content", "")) for r in resp["results"]]

    return search


def duckduckgo_backend(query: str, n: int, retry_delay: float = 1.5) -> list[Hit]:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        rows = DDGS(timeout=10).text(query, max_results=n)
    except DDGSException:  # usually a transient rate limit ("No results found."): retry once
        time.sleep(retry_delay)
        rows = DDGS(timeout=10).text(query, max_results=n)
    return [Hit(r.get("title", ""), r.get("href", ""), r.get("body", "")) for r in rows]


def format_hits(query: str, hits: list[Hit]) -> str:
    if not hits:
        return f"No results for '{query}'."
    out = ""
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h.content.split())[:SNIPPET_CHARS]
        block = f"[{i}] {h.title}\n    {h.url}\n    {snippet}\n\n"
        if len(out) + len(block) > TOTAL_CHARS - len(CITE):
            break
        out += block
    return out + CITE


class WebSearch:
    def __init__(
        self,
        tavily_api_key: str | None,
        audit: AuditLog,
        *,
        tavily: Backend | None = None,
        duckduckgo: Backend = duckduckgo_backend,
    ):
        self._tavily = tavily or (tavily_backend(tavily_api_key) if tavily_api_key else None)
        self._duckduckgo = duckduckgo
        self.audit = audit

    @property
    def mode(self) -> str:
        return "Tavily (DuckDuckGo fallback)" if self._tavily else "DuckDuckGo (fallback mode: no Tavily key)"

    def search(self, query: str) -> SearchResults:
        reason = "no Tavily API key"
        if self._tavily:
            try:
                return SearchResults(format_hits(query, self._tavily(query, MAX_RESULTS)), "tavily", False)
            except Exception as e:  # network, quota, bad key, ...: degrade, don't fail
                reason = f"Tavily failed: {type(e).__name__}: {e}"
                self.audit.record("search_fallback", query=query, reason=reason)
        try:
            hits = self._duckduckgo(query, MAX_RESULTS)
        except Exception as e:
            return SearchResults(
                f"Web search is unavailable right now ({reason}; DuckDuckGo failed: {e}).", "none", False
            )
        return SearchResults(format_hits(query, hits), "duckduckgo", True)


def web_tools(search: WebSearch) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    def web_search(query: str) -> tuple[str, dict]:
        """Search the web for current or real-time information: news, weather, prices, recent
        events, or facts you are unsure of. Returns numbered results with URLs to cite."""
        r = search.search(query)
        return r.text, {"backend": r.backend, "fallback": r.fallback}

    return [web_search]
