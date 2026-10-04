"""Shared overview request lifecycle, independent of Textual rendering."""

import asyncio
from typing import Literal

import pytest

from rit.state.models import LoadingState
from rit.state.overview_resource import OverviewResource


async def test_resource_coalesces_loads_and_retries_failed_refreshes() -> None:
    resource = OverviewResource[str, str]()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []
    changes: list[LoadingState] = []

    async def fetch(key: str) -> str:
        calls.append(key)
        started.set()
        await release.wait()
        return key

    def changed() -> None:
        changes.append(resource.loading)

    async def load(*, refresh: bool = False) -> None:
        await resource.load(
            current_key=lambda: "head",
            fetch=fetch,
            on_change=changed,
            refresh=refresh,
        )

    first = asyncio.create_task(load())
    await asyncio.wait_for(started.wait(), timeout=3)
    await load(refresh=True)
    assert calls == ["head"]
    assert resource.loaded_value is None
    release.set()
    await first
    await load()
    assert calls == ["head"]
    assert resource.loaded_value == "head"
    assert changes == [LoadingState.LOADING, LoadingState.LOADED]

    async def denied(_key: str) -> str:
        raise RuntimeError("permission denied")

    await resource.load(
        current_key=lambda: "head",
        fetch=denied,
        on_change=changed,
        refresh=True,
    )
    assert resource.loading == LoadingState.ERROR
    assert resource.error == "permission denied"
    assert resource.value == "head" and resource.loaded_value is None
    await load()
    assert calls == ["head", "head"]
    assert resource.loaded_value == "head" and resource.error is None


@pytest.mark.parametrize("completion", ["success", "failure", "cancel"])
async def test_superseded_request_cannot_overwrite_a_reused_revision(
    completion: Literal["success", "failure", "cancel"],
) -> None:
    resource = OverviewResource[str, str]()
    key = "old"
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []
    changes: list[LoadingState] = []

    async def fetch(head: str) -> str:
        calls.append(head)
        request = len(calls)
        if request == 1:
            started.set()
            await release.wait()
            if completion == "failure":
                raise RuntimeError("obsolete error")
        return f"{head}:{request}"

    def changed() -> None:
        changes.append(resource.loading)

    async def load() -> None:
        await resource.load(
            current_key=lambda: key,
            fetch=fetch,
            on_change=changed,
        )

    old = asyncio.create_task(load())
    await asyncio.wait_for(started.wait(), timeout=3)
    key = "new"
    await load()
    key = "old"
    await load()
    assert resource.loaded_value == "old:3"
    if completion == "cancel":
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
    else:
        release.set()
        await old
    assert calls == ["old", "new", "old"]
    assert resource.loaded_value == "old:3" and resource.error is None
    assert changes == [
        LoadingState.LOADING,
        LoadingState.LOADING,
        LoadingState.LOADED,
        LoadingState.LOADING,
        LoadingState.LOADED,
    ]


async def test_response_requires_the_live_revision_even_without_a_new_request() -> None:
    resource = OverviewResource[str, str]()
    key = "old"
    started = asyncio.Event()
    release = asyncio.Event()
    changes: list[LoadingState] = []

    async def fetch(head: str) -> str:
        started.set()
        await release.wait()
        return head

    def changed() -> None:
        changes.append(resource.loading)

    task = asyncio.create_task(
        resource.load(current_key=lambda: key, fetch=fetch, on_change=changed)
    )
    await asyncio.wait_for(started.wait(), timeout=3)
    key = "new"
    release.set()
    await task
    assert resource.value is None and resource.loaded_value is None
    assert changes == [LoadingState.LOADING]
    await resource.load(current_key=lambda: key, fetch=fetch, on_change=changed)
    assert resource.loaded_value == "new"


async def test_cancelled_current_request_notifies_idle_and_can_restart() -> None:
    resource = OverviewResource[str, str]()
    started = asyncio.Event()
    release = asyncio.Event()
    changes: list[LoadingState] = []

    async def fetch(key: str) -> str:
        started.set()
        await release.wait()
        return key

    def changed() -> None:
        changes.append(resource.loading)

    task = asyncio.create_task(
        resource.load(current_key=lambda: "head", fetch=fetch, on_change=changed)
    )
    await asyncio.wait_for(started.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert resource.loading == LoadingState.IDLE and resource.error is None
    assert changes == [LoadingState.LOADING, LoadingState.IDLE]
    release.set()
    await resource.load(current_key=lambda: "head", fetch=fetch, on_change=changed)
    assert resource.loaded_value == "head"
    assert changes[-2:] == [LoadingState.LOADING, LoadingState.LOADED]
