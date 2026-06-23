from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping


__all__ = (
    "FileContentFetcher",
    "load_cached_file_content",
)


FileContentFetcher = Callable[[str, str], Awaitable[str]]


async def load_cached_file_content(
    cache: MutableMapping[str, str],
    *,
    filename: str,
    head_sha: str,
    fetch: FileContentFetcher | None,
) -> str | None:
    """Return cached file content, fetching and caching it when possible."""
    cached = cache.get(filename)
    if cached is not None:
        return cached
    if not head_sha:
        return None
    if fetch is None:
        return None

    try:
        content = await fetch(filename, head_sha)
    except RuntimeError:
        return None
    cache[filename] = content
    return content
