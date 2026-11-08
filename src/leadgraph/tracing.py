"""Optional Langfuse tracing: one trace per company, LLM calls nested under it.

Enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (and the `eval` extra
is installed). The SDK reads its keys from the environment; they are never logged here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_handler: Any = None


def enabled() -> bool:
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return False
    try:
        import langfuse  # noqa: F401
    except ImportError:
        return False
    return True


def callbacks() -> list[Any]:
    """LangChain callbacks for an LLM call ([] when tracing is off)."""
    global _handler
    if not enabled():
        return []
    if _handler is None:
        from langfuse.langchain import CallbackHandler

        _handler = CallbackHandler()
    return [_handler]


@contextmanager
def company_trace(name: str, **metadata: Any) -> Iterator[None]:
    """Group everything inside (agent turns, tool results, LLM calls) into one trace."""
    if not enabled():
        yield
        return
    from langfuse import get_client

    with get_client().start_as_current_observation(name=f"company: {name}", as_type="agent",
                                                   metadata=metadata or None):
        yield


def flush() -> None:
    if enabled():
        from langfuse import get_client

        get_client().flush()
