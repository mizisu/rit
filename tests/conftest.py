"""Pytest configuration and fixtures.

This file patches Textual 7.x CSS bug before any tests run.
"""

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def pytest_configure(config):
    """Pytest hook called before test collection.

    This is the earliest point where we can apply the patch.
    """
    _patch_textual_markdown_css()


def _patch_textual_markdown_css() -> None:
    """Patch Textual 7.x MarkdownTableContent CSS bug.

    Textual 7.x has a bug where MarkdownTableContent's DEFAULT_CSS contains
    an invalid keyline value: `keyline: thin $foreground 20%;`
    The keyline property only accepts 2 values: <type> <color>
    """
    try:
        from textual.widgets._markdown import MarkdownTableContent

        if "keyline: thin $foreground 20%" in MarkdownTableContent.DEFAULT_CSS:
            MarkdownTableContent.DEFAULT_CSS = MarkdownTableContent.DEFAULT_CSS.replace(
                "keyline: thin $foreground 20%;",
                "keyline: thin $foreground;",
            )
    except ImportError:
        pass


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
