from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar


__all__ = (
    "OrderedPageItems",
    "PRFilePageProgress",
    "PR_FILES_MAX_REST_PAGES",
    "PR_FILES_PAGE_CONCURRENCY",
    "PR_FILES_PER_PAGE",
    "collect_all_page_items",
    "collect_ordered_page_items",
    "collect_page_batches",
)


PR_FILES_PER_PAGE = 100
PR_FILES_MAX_REST_PAGES = 30
PR_FILES_PAGE_CONCURRENCY = 6
PageItem = TypeVar("PageItem")
PageBatch = TypeVar("PageBatch")


@dataclass(frozen=True)
class OrderedPageItems(Generic[PageItem]):
    """Items collected from ordered REST pages and whether the last page was seen."""

    items: tuple[PageItem, ...]
    saw_last_page: bool


@dataclass(frozen=True)
class PRFilePageProgress:
    """Pagination policy for GitHub PR file REST pages."""

    loaded_count: int
    total_count_hint: int = 0
    changed_files: int = 0
    per_page: int = PR_FILES_PER_PAGE

    @property
    def known_total(self) -> int | None:
        if self.changed_files > 0:
            return self.changed_files
        if self.total_count_hint > self.loaded_count:
            return self.total_count_hint
        return None

    @property
    def rest_limit_exceeded(self) -> bool:
        total = self.known_total
        if total is None:
            return False
        return (
            total > PR_FILES_MAX_REST_PAGES * self.per_page
            and self.loaded_count < total
        )

    def initial_remaining_pages(self) -> tuple[int, ...]:
        if self.known_total is not None:
            return self.remaining_known_pages()

        known_pages = self.remaining_known_pages()
        if known_pages:
            return known_pages
        if self.loaded_count < self.per_page:
            return ()
        return tuple(range(2, PR_FILES_MAX_REST_PAGES + 1))

    def remaining_known_pages(self) -> tuple[int, ...]:
        if self.loaded_count < self.per_page:
            return ()

        total = self.known_total
        if total is None or total <= self.per_page:
            return ()

        page_count = min(
            PR_FILES_MAX_REST_PAGES,
            (total + self.per_page - 1) // self.per_page,
        )
        return tuple(range(2, page_count + 1))

    def next_page_chunk(
        self,
        remaining_pages: Sequence[int],
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        if not remaining_pages:
            return (), ()

        if self.known_total is not None:
            pages = tuple(
                page
                for page in self.remaining_known_pages()
                if page >= remaining_pages[0]
            )
            return pages, ()

        return (
            tuple(remaining_pages[:PR_FILES_PAGE_CONCURRENCY]),
            tuple(remaining_pages[PR_FILES_PAGE_CONCURRENCY:]),
        )

    def rest_list_complete(self, *, saw_last_page: bool) -> bool:
        total = self.known_total
        if total is not None and self.loaded_count >= total:
            return True
        return saw_last_page


def collect_ordered_page_items(
    page_chunk: Sequence[int],
    page_batches: Mapping[int, Sequence[PageItem]],
    *,
    per_page: int = PR_FILES_PER_PAGE,
) -> OrderedPageItems[PageItem]:
    """Collect page items in order until an empty or short page is encountered."""
    items: list[PageItem] = []
    saw_last_page = False
    for page in page_chunk:
        batch = page_batches.get(page, ())
        if not batch:
            saw_last_page = True
            break
        items.extend(batch)
        if len(batch) < per_page:
            saw_last_page = True
            break
    return OrderedPageItems(items=tuple(items), saw_last_page=saw_last_page)


async def collect_page_batches(
    pages: Sequence[int],
    fetch_page: Callable[[int], Awaitable[PageBatch]],
    *,
    concurrency: int = PR_FILES_PAGE_CONCURRENCY,
) -> dict[int, PageBatch]:
    """Fetch page batches concurrently and return them by page number."""
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_limited(page: int) -> tuple[int, PageBatch]:
        async with semaphore:
            return page, await fetch_page(page)

    return dict(await asyncio.gather(*(fetch_limited(page) for page in pages)))


async def collect_all_page_items(
    first_page: Sequence[PageItem],
    fetch_pages: Callable[[tuple[int, ...]], Awaitable[Mapping[int, Sequence[PageItem]]]],
    *,
    total_count_hint: int = 0,
    per_page: int = PR_FILES_PER_PAGE,
) -> tuple[PageItem, ...]:
    """Collect ordered REST page items after an already-fetched first page."""
    if len(first_page) < per_page:
        return tuple(first_page)

    items = list(first_page)
    remaining_pages = PRFilePageProgress(
        loaded_count=len(first_page),
        total_count_hint=total_count_hint,
        per_page=per_page,
    ).initial_remaining_pages()

    while remaining_pages:
        page_chunk, remaining_pages = PRFilePageProgress(
            loaded_count=len(items),
            total_count_hint=total_count_hint,
            per_page=per_page,
        ).next_page_chunk(remaining_pages)
        if not page_chunk:
            break

        page_batches = await fetch_pages(page_chunk)
        collected = collect_ordered_page_items(
            page_chunk,
            page_batches,
            per_page=per_page,
        )
        items.extend(collected.items)
        if collected.saw_last_page:
            break

    return tuple(items)
