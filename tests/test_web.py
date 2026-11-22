"""Web search: Tavily first, graceful DuckDuckGo fallback, bounded output."""

from __future__ import annotations

import httpx
import pytest

from aura.audit import AuditLog
from aura.config import Settings
from aura.tools.web import (
    CITE,
    SNIPPET_CHARS,
    TOTAL_CHARS,
    Hit,
    WebSearch,
    duckduckgo_backend,
    tavily_backend,
    web_tools,
)

HITS = [Hit("Met Office", "https://metoffice.example/london", "London: 11°C, cloudy.")]


class Spy:
    def __init__(self, result=HITS, error: Exception | None = None):
        self.result, self.error, self.queries = result, error, []

    def __call__(self, query: str, n: int) -> list[Hit]:
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def audit(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


def test_no_tavily_key_uses_duckduckgo_and_flags_fallback(audit):
    ddg = Spy()
    r = WebSearch(None, audit, duckduckgo=ddg).search("weather London")
    assert (r.backend, r.fallback) == ("duckduckgo", True)
    assert ddg.queries == ["weather London"]
    assert "https://metoffice.example/london" in r.text


def test_tavily_is_primary_when_configured(audit):
    tavily, ddg = Spy(), Spy()
    r = WebSearch("tvly-key", audit, tavily=tavily, duckduckgo=ddg).search("weather London")
    assert (r.backend, r.fallback) == ("tavily", False)
    assert ddg.queries == []  # fallback not touched


@pytest.mark.parametrize("error", [TimeoutError("slow"), RuntimeError("401 invalid key"), KeyError("results")])
def test_tavily_errors_degrade_to_duckduckgo(audit, error):
    ddg = Spy()
    r = WebSearch("tvly-key", audit, tavily=Spy(error=error), duckduckgo=ddg).search("q")
    assert (r.backend, r.fallback) == ("duckduckgo", True)
    assert audit.entries()[-1]["event"] == "search_fallback"


def test_both_backends_failing_is_reported_not_raised(audit):
    r = WebSearch("k", audit, tavily=Spy(error=RuntimeError("down")), duckduckgo=Spy(error=RuntimeError("ratelimit"))).search("q")
    assert r.backend == "none" and not r.fallback
    assert "unavailable" in r.text and "ratelimit" in r.text


def test_results_are_bounded_for_the_context_window(audit):
    many = [Hit(f"Title {i}", f"https://e.example/{i}", "word " * 1000) for i in range(5)]
    r = WebSearch(None, audit, duckduckgo=Spy(result=many)).search("q")
    assert len(r.text) <= TOTAL_CHARS
    assert all(len(line) <= SNIPPET_CHARS + 4 for line in r.text.splitlines())


def test_tool_reports_backend_as_an_artifact(audit):
    (tool,) = web_tools(WebSearch(None, audit, duckduckgo=Spy()))
    msg = tool.invoke({"type": "tool_call", "name": "web_search", "args": {"query": "q"}, "id": "1"})
    assert msg.artifact == {"backend": "duckduckgo", "fallback": True}


def test_duckduckgo_retries_once_on_a_transient_failure(monkeypatch):
    import ddgs
    from ddgs.exceptions import DDGSException

    attempts = []

    class FlakyDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results):
            attempts.append(query)
            if len(attempts) == 1:
                raise DDGSException("No results found.")
            return [{"title": "T", "href": "https://e.example", "body": "B"}]

    monkeypatch.setattr(ddgs, "DDGS", FlakyDDGS)
    assert duckduckgo_backend("q", 3, retry_delay=0) == [Hit("T", "https://e.example", "B")]
    assert len(attempts) == 2


def test_results_ask_the_model_to_cite_sources(audit):
    r = WebSearch(None, audit, duckduckgo=Spy()).search("q")
    assert r.text.endswith(CITE)


def _online() -> bool:
    try:
        httpx.get("https://duckduckgo.com", timeout=3)
        return True
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _online(), reason="offline")
def test_live_duckduckgo_returns_results():
    hits = duckduckgo_backend("weather London", 3)
    assert hits and all(h.url.startswith("http") for h in hits)


@pytest.mark.skipif(not Settings.from_env().tavily_api_key, reason="no TAVILY_API_KEY")
def test_live_tavily_returns_results():
    hits = tavily_backend(Settings.from_env().tavily_api_key)("weather London", 3)
    assert hits and all(h.url.startswith("http") and h.content for h in hits)
