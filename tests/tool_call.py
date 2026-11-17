"""Call an async MCP tool function from a synchronous test."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


def call(fn: Callable[..., Awaitable[Any]], /, *args: Any, **kwargs: Any) -> Any:
    """Run `await fn(*args, **kwargs)` on a fresh event loop and return the result."""
    return asyncio.run(fn(*args, **kwargs))
