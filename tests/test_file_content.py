import asyncio

import pytest

from rit.state.file_content import load_cached_file_content
from rit.state.models import PR
from rit.state.store import PRStore


async def test_load_cached_file_content_returns_cached_without_fetching() -> None:
    cache = {("deadbeef", "src/app.py"): "cached content"}
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, ref: str) -> str:
        calls.append((path, ref))
        return "fresh content"

    result = await load_cached_file_content(
        cache, filename="src/app.py", ref="deadbeef", fetch=fetch
    )

    assert result == "cached content"
    assert calls == []


async def test_load_cached_file_content_requires_ref() -> None:
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, ref: str) -> str:
        calls.append((path, ref))
        return "fresh content"

    result = await load_cached_file_content(
        {}, filename="src/app.py", ref="", fetch=fetch
    )

    assert result is None
    assert calls == []


async def test_load_cached_file_content_allows_missing_fetcher_for_cached_content() -> (
    None
):
    result = await load_cached_file_content(
        {("deadbeef", "src/app.py"): "cached content"},
        filename="src/app.py",
        ref="deadbeef",
        fetch=None,
    )

    assert result == "cached content"


async def test_load_cached_file_content_returns_none_when_fetcher_is_missing() -> None:
    result = await load_cached_file_content(
        {}, filename="src/app.py", ref="deadbeef", fetch=None
    )

    assert result is None


async def test_load_cached_file_content_fetches_and_caches_content() -> None:
    cache: dict[tuple[str, str], str] = {}
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, ref: str) -> str:
        calls.append((path, ref))
        return "fresh content"

    result = await load_cached_file_content(
        cache, filename="src/app.py", ref="deadbeef", fetch=fetch
    )

    assert result == "fresh content"
    assert cache == {("deadbeef", "src/app.py"): "fresh content"}
    assert calls == [("src/app.py", "deadbeef")]


async def test_load_cached_file_content_swallows_fetch_errors() -> None:
    cache: dict[tuple[str, str], str] = {}

    async def fetch(path: str, ref: str) -> str:
        raise RuntimeError("boom")

    result = await load_cached_file_content(
        cache, filename="src/app.py", ref="deadbeef", fetch=fetch
    )

    assert result is None
    assert cache == {}


async def test_load_cached_file_content_reraises_non_runtime_fetch_errors() -> None:
    cache: dict[tuple[str, str], str] = {}

    async def fetch(path: str, ref: str) -> str:
        raise ValueError("bad fetch adapter state")

    with pytest.raises(ValueError, match="bad fetch adapter state"):
        await load_cached_file_content(
            cache, filename="src/app.py", ref="deadbeef", fetch=fetch
        )

    assert cache == {}


async def test_store_source_cache_separates_refs_and_rename_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=123, head_sha="head-sha")
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, ref: str) -> str:
        calls.append((path, ref))
        return f"{ref}:{path}"

    monkeypatch.setattr(store._service, "get_file_content", fetch)

    assert await store.get_file_content("new.py") == "head-sha:new.py"
    assert (
        await store.get_file_content("new.py", ref="merge-base-sha")
        == "merge-base-sha:new.py"
    )
    assert (
        await store.get_file_content("old.py", ref="merge-base-sha")
        == "merge-base-sha:old.py"
    )
    assert await store.get_file_content("new.py") == "head-sha:new.py"
    assert (
        await store.get_file_content("old.py", ref="merge-base-sha")
        == "merge-base-sha:old.py"
    )

    store.state.pr.head_sha = "next-head-sha"
    assert await store.get_file_content("new.py") == "next-head-sha:new.py"
    assert calls == [
        ("new.py", "head-sha"),
        ("new.py", "merge-base-sha"),
        ("old.py", "merge-base-sha"),
        ("new.py", "next-head-sha"),
    ]


async def test_store_discards_late_preview_after_head_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=123, head_sha="before")
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(path: str, ref: str) -> str:
        started.set()
        await release.wait()
        return ref

    monkeypatch.setattr(store._service, "get_file_content", fetch)
    request = asyncio.create_task(store.get_file_content("source.py"))
    await started.wait()
    store.state.pr = PR(number=123, head_sha="after")
    release.set()
    assert await request is None
    assert store.state.file_contents == {("before", "source.py"): "before"}
    assert await store.get_file_content("source.py", ref="before") == "before"
    assert await store.get_file_content("source.py") == "after"
