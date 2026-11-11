"""Shared pytest helpers."""

import asyncio
from collections.abc import Callable
from typing import Literal


async def wait_until[T](
    predicate: Callable[[], T | None | Literal[False]],
    *,
    timeout: float = 2.0,
) -> T:
    """Wait for a truthy predicate without busy-polling the UI event loop."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.001)
