import asyncio
import builtins

import pytest

import rit.core.pagination as pr_file_pagination
from rit.core.pagination import (
    PRFilePageProgress,
    collect_all_page_items,
    collect_ordered_page_items,
    collect_page_batches,
)


def test_file_page_progress_uses_known_total_for_remaining_pages() -> None:
    progress = PRFilePageProgress(loaded_count=100, total_count_hint=250)

    assert progress.known_total == 250
    assert progress.initial_remaining_pages() == (2, 3)
    assert progress.next_page_chunk(tuple(range(2, 31))) == ((2, 3), ())


def test_file_page_progress_known_next_chunk_avoids_rebuilding_known_pages() -> None:
    class Progress(PRFilePageProgress):
        def remaining_known_pages(self) -> tuple[int, ...]:
            raise AssertionError("next chunk should filter the provided pages directly")

    progress = Progress(loaded_count=100, total_count_hint=250)

    assert progress.next_page_chunk(tuple(range(2, 31))) == ((2, 3), ())


def test_file_page_progress_known_next_chunk_reuses_complete_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = (2, 3)
    progress = PRFilePageProgress(loaded_count=100, total_count_hint=250)

    monkeypatch.setattr(
        pr_file_pagination,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete known page chunk should not be rebuilt")
        ),
        raising=False,
    )

    page_chunk, remaining_pages = progress.next_page_chunk(pages)

    assert page_chunk is pages
    assert remaining_pages == builtins.tuple()


def test_file_page_progress_probes_until_empty_when_total_is_unknown() -> None:
    progress = PRFilePageProgress(loaded_count=100)

    assert progress.known_total is None
    assert progress.initial_remaining_pages() == tuple(range(2, 31))
    assert progress.next_page_chunk(tuple(range(2, 31))) == (
        tuple(range(2, 8)),
        tuple(range(8, 31)),
    )


def test_file_page_progress_unknown_next_chunk_reuses_complete_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = (2, 3)
    progress = PRFilePageProgress(loaded_count=100)

    monkeypatch.setattr(
        pr_file_pagination,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete unknown page chunk should not be rebuilt")
        ),
        raising=False,
    )

    page_chunk, remaining_pages = progress.next_page_chunk(pages)

    assert page_chunk is pages
    assert remaining_pages == builtins.tuple()


def test_file_page_progress_stops_when_first_page_is_not_full() -> None:
    progress = PRFilePageProgress(loaded_count=25)

    assert progress.initial_remaining_pages() == ()
    assert progress.rest_list_complete(saw_last_page=True) is True


def test_file_page_progress_detects_rest_limit_overflow() -> None:
    progress = PRFilePageProgress(loaded_count=100, changed_files=3001)

    assert progress.rest_limit_exceeded is True
    assert progress.rest_list_complete(saw_last_page=False) is False


def test_file_page_progress_prefers_changed_files_over_display_hint() -> None:
    progress = PRFilePageProgress(
        loaded_count=100,
        total_count_hint=150,
        changed_files=101,
    )

    assert progress.known_total == 101
    assert progress.initial_remaining_pages() == (2,)


def test_file_page_progress_does_not_probe_after_one_known_full_page() -> None:
    progress = PRFilePageProgress(loaded_count=100, changed_files=100)

    assert progress.initial_remaining_pages() == ()


def test_collect_ordered_page_items_skips_tuple_for_empty_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pr_file_pagination,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty ordered page collection should reuse empty items")
        ),
        raising=False,
    )

    result = collect_ordered_page_items((), {})

    assert result.items == builtins.tuple()
    assert result.saw_last_page is False


def test_collect_ordered_page_items_stops_at_empty_page() -> None:
    result = collect_ordered_page_items(
        (2, 3, 4),
        {
            2: ("a.py",),
            3: (),
            4: ("ignored.py",),
        },
    )

    assert result.items == ("a.py",)
    assert result.saw_last_page is True


def test_collect_ordered_page_items_stops_after_short_page() -> None:
    result = collect_ordered_page_items(
        (2, 3),
        {
            2: tuple(f"file-{index}.py" for index in range(99)),
            3: ("ignored.py",),
        },
    )

    assert len(result.items) == 99
    assert result.saw_last_page is True


def test_collect_ordered_page_items_collects_full_pages() -> None:
    result = collect_ordered_page_items(
        (2, 3),
        {
            2: tuple(f"file-{index}.py" for index in range(100)),
            3: tuple(f"file-{index}.py" for index in range(100, 200)),
        },
    )

    assert len(result.items) == 200
    assert result.saw_last_page is False


def test_collect_ordered_page_items_reuses_single_tuple_page_batch() -> None:
    batch = tuple(f"file-{index}.py" for index in range(100))

    result = collect_ordered_page_items((2,), {2: batch})

    assert result.items is batch
    assert result.saw_last_page is False


def test_collect_ordered_page_items_single_item_batch_skips_tuple_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pr_file_pagination,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single item page batches should not call tuple")
        ),
        raising=False,
    )

    result = collect_ordered_page_items((2,), {2: ["one.py"]})

    assert result.items == ("one.py",)
    assert result.saw_last_page is True


@pytest.mark.asyncio
async def test_collect_page_batches_returns_batches_by_page() -> None:
    calls: list[int] = []

    async def fetch_page(page: int) -> list[str]:
        calls.append(page)
        return [f"file-{page}.py"]

    batches = await collect_page_batches(
        (2, 3),
        fetch_page,
        concurrency=2,
    )

    assert calls == [2, 3]
    assert batches == {
        2: ["file-2.py"],
        3: ["file-3.py"],
    }


@pytest.mark.asyncio
async def test_collect_page_batches_skips_scheduler_for_empty_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch_page(page: int) -> list[str]:
        raise AssertionError(f"unexpected fetch for page {page}")

    monkeypatch.setattr(
        pr_file_pagination.asyncio,
        "Semaphore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty page batch should not create a scheduler")
        ),
    )

    assert await collect_page_batches((), fetch_page) == {}


@pytest.mark.asyncio
async def test_collect_page_batches_skips_scheduler_for_single_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def fetch_page(page: int) -> list[str]:
        calls.append(page)
        return [f"file-{page}.py"]

    monkeypatch.setattr(
        pr_file_pagination.asyncio,
        "Semaphore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single page batch should not create a scheduler")
        ),
    )

    assert await collect_page_batches((2,), fetch_page) == {2: ["file-2.py"]}
    assert calls == [2]


@pytest.mark.asyncio
async def test_collect_page_batches_limits_concurrency() -> None:
    running = 0
    max_running = 0

    async def fetch_page(page: int) -> list[int]:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        return [page]

    batches = await collect_page_batches(
        tuple(range(2, 8)),
        fetch_page,
        concurrency=2,
    )

    assert max_running == 2
    assert batches == {page: [page] for page in range(2, 8)}


@pytest.mark.asyncio
async def test_collect_all_page_items_returns_short_first_page_without_fetching() -> (
    None
):
    calls: list[tuple[int, ...]] = []

    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[str]]:
        calls.append(pages)
        return {}

    items = await collect_all_page_items(
        ("file-1.py",),
        fetch_pages,
        per_page=2,
    )

    assert items == ("file-1.py",)
    assert calls == []


@pytest.mark.asyncio
async def test_collect_all_page_items_reuses_empty_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[str]]:
        raise AssertionError(f"unexpected fetch for pages {pages}")

    monkeypatch.setattr(
        pr_file_pagination,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty first page should not be rebuilt")
        ),
        raising=False,
    )

    assert await collect_all_page_items([], fetch_pages) == builtins.tuple()


@pytest.mark.asyncio
async def test_collect_all_page_items_reuses_short_tuple_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = ("file-1.py",)

    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[str]]:
        raise AssertionError(f"unexpected fetch for pages {pages}")

    monkeypatch.setattr(
        pr_file_pagination,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("short tuple first page should not be rebuilt")
        ),
        raising=False,
    )

    items = await collect_all_page_items(
        first_page,
        fetch_pages,
        per_page=2,
    )

    assert items is first_page


@pytest.mark.asyncio
async def test_collect_all_page_items_avoids_list_copy_for_complete_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = ("file-1.py", "file-2.py")

    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[str]]:
        raise AssertionError("complete first page should not fetch more pages")

    monkeypatch.setattr(
        pr_file_pagination,
        "list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete first page should not build an item list")
        ),
        raising=False,
    )

    items = await collect_all_page_items(
        first_page,
        fetch_pages,
        total_count_hint=2,
        per_page=2,
    )

    assert items is first_page


@pytest.mark.asyncio
async def test_collect_all_page_items_fetches_known_remaining_pages() -> None:
    calls: list[tuple[int, ...]] = []

    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[str]]:
        calls.append(pages)
        return {
            2: ["file-3.py", "file-4.py"],
            3: ["file-5.py"],
        }

    items = await collect_all_page_items(
        ("file-1.py", "file-2.py"),
        fetch_pages,
        total_count_hint=5,
        per_page=2,
    )

    assert calls == [(2, 3)]
    assert items == (
        "file-1.py",
        "file-2.py",
        "file-3.py",
        "file-4.py",
        "file-5.py",
    )


@pytest.mark.asyncio
async def test_collect_all_page_items_reuses_initial_progress_for_known_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, ...]] = []
    progress_calls = 0
    real_progress = pr_file_pagination.PRFilePageProgress

    def counting_progress(*args, **kwargs):
        nonlocal progress_calls
        progress_calls += 1
        return real_progress(*args, **kwargs)

    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[str]]:
        calls.append(pages)
        return {
            2: ["file-3.py", "file-4.py"],
            3: ["file-5.py"],
        }

    monkeypatch.setattr(
        pr_file_pagination,
        "PRFilePageProgress",
        counting_progress,
    )

    items = await collect_all_page_items(
        ("file-1.py", "file-2.py"),
        fetch_pages,
        total_count_hint=5,
        per_page=2,
    )

    assert progress_calls == 1
    assert calls == [(2, 3)]
    assert items[-1] == "file-5.py"


@pytest.mark.asyncio
async def test_collect_all_page_items_stops_after_empty_unknown_page_chunk() -> None:
    calls: list[tuple[int, ...]] = []

    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[str]]:
        calls.append(pages)
        return {2: ["file-3.py"], 3: []}

    items = await collect_all_page_items(
        ("file-1.py", "file-2.py"),
        fetch_pages,
        per_page=2,
    )

    assert calls == [(2, 3, 4, 5, 6, 7)]
    assert items == ("file-1.py", "file-2.py", "file-3.py")
