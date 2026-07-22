"""Shared pytest helpers."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def wait_until(
    predicate: Callable[[], T | None | bool],
    *,
    timeout: float = 0.25,
) -> T:
    """Wait until a test predicate returns a truthy value."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0)
