"""Web_Search_Tool: Tavily first; DuckDuckGo when there is no Tavily key or Tavily fails.

Every search is gated (egress.py): it runs only with a grant from an approval card the user
answered for this exact query, and a query with characters the card can't show is refused.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from aura.audit import AuditLog
from aura.connections.actions import untrusted
from aura.tools.egress import EgressGate, HiddenCharacters, PermissionDenied

FALLBACK_NOTICE = "I'm using my fallback search mode to find that for you."
MAX_RESULTS = 5
SNIPPET_CHARS = 600
TOTAL_CHARS = 4_000  # ~1k tokens
CITE = "Cite the URL of each result you use. If these results don't answer the question, say so."
RESULTS_SOURCE = "web search results"  # R5: what the web says is information, never instructions
_LABEL_CHARS = len(untrusted(RESULTS_SOURCE, ""))
REPHRASE = ("Not searched: the query has characters that can't be shown on the approval card ({codes}). "
            "Write the query again in plain visible text.")
NOT_APPROVED = "Not searched: {reason} Nothing left this Mac."


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
    """The results, labelled as untrusted information (R5), then Aura's own instruction to cite."""
    if not hits:
        return f"No results for '{query}'."
    room = TOTAL_CHARS - len(CITE) - _LABEL_CHARS
    out = ""
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h.content.split())[:SNIPPET_CHARS]
        block = f"[{i}] {h.title}\n    {h.url}\n    {snippet}\n\n"
        if len(out) + len(block) > room:
            break
        out += block
    return untrusted(RESULTS_SOURCE, out) + CITE


class WebSearch:
    def __init__(
        self,
        tavily_api_key: str | None,
        audit: AuditLog,
        *,
        tavily: Backend | None = None,
        duckduckgo: Backend = duckduckgo_backend,
        gate: EgressGate | None = None,
    ):
        self._tavily = tavily or (tavily_backend(tavily_api_key) if tavily_api_key else None)
        self._duckduckgo = duckduckgo
        self.audit = audit
        self.gate = gate or EgressGate()

    def set_key(self, tavily_api_key: str | None) -> None:
        """A Tavily key saved (or removed) in Settings → Keys takes effect at once."""
        self._tavily = tavily_backend(tavily_api_key) if tavily_api_key else None

    @property
    def mode(self) -> str:
        return "Tavily (DuckDuckGo fallback)" if self._tavily else "DuckDuckGo (fallback mode: no Tavily key)"

    @property
    def destinations(self) -> str:
        """Where an approved query goes, for the approval card."""
        return "Tavily; DuckDuckGo if Tavily fails" if self._tavily else "DuckDuckGo"

    def search(self, query: str, *, tool_call_id: str | None = None) -> SearchResults:
        """Raises PermissionDenied, before any backend runs, unless the user approved this exact
        query for this tool call. One grant covers Tavily and its DuckDuckGo fallback."""
        self.gate.consume(tool_call_id, query)
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
    def web_search(query: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> tuple[str, dict]:
        """Search the web for current or real-time information: news, weather, prices, recent
        events, or facts you are unsure of. Returns numbered results with URLs to cite. Each
        search shows the user an approval card with the exact query: that card is the
        confirmation, so call the tool directly instead of asking in chat first."""
        try:
            r = search.search(query, tool_call_id=tool_call_id)
        except PermissionDenied as e:  # fail closed and say so: hidden characters (no card was
            # shown: brain.py), or a skipped or altered approval
            search.audit.record("egress_refused", tool="web_search", reason=str(e))
            hidden = isinstance(e, HiddenCharacters)
            text = REPHRASE.format(codes=", ".join(e.codes)) if hidden else NOT_APPROVED.format(reason=e)
            return text, {"backend": "none", "fallback": False}
        return r.text, {"backend": r.backend, "fallback": r.fallback}

    return [web_search]
