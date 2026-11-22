"""UI events (status lines, notices, settings changes) sent to whoever is streaming the agent."""

from __future__ import annotations

from langgraph.config import get_stream_writer


def emit(event: dict) -> None:
    try:
        get_stream_writer()(event)
    except Exception:  # not inside a streamed run (e.g. a direct tool call in a test)
        pass
