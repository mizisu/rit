from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping

FileContentFetcher = Callable[[str, str], Awaitable[str]]


async def load_cached_file_content(
    cache: MutableMapping[tuple[str, str], str],
    *,
    filename: str,
    ref: str,
    fetch: FileContentFetcher | None,
) -> str | None:
    """Cache source text by ref and path, separately from canonical diffs."""
    key = (ref, filename)
    cached = cache.get(key)
    if cached is not None:
        return cached
    if not ref:
        return None
    if fetch is None:
        return None
    try:
        content = await fetch(filename, ref)
    except RuntimeError:
        return None
    cache[key] = content
    return content
