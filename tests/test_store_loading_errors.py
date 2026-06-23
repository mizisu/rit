import pytest

from rit.state.models import PRFile
from rit.state.store import PRStore


class BrokenSummaryAdapter:
    async def get_pr_summary(self, pr_number: int):
        raise ValueError("bad summary adapter state")


class BrokenFilePageAdapter:
    async def get_pr_files_page(
        self,
        pr_number: int,
        *,
        page: int,
        per_page: int = 100,
    ) -> list[PRFile]:
        raise ValueError("bad file page adapter state")

    async def get_pr_file_pages(
        self,
        pr_number: int,
        *,
        pages: tuple[int, ...],
        per_page: int = 100,
    ) -> dict[int, list[PRFile]]:
        raise AssertionError("remaining pages should not be fetched")


class BrokenFileViewStateAdapter:
    async def get_pr_file_view_states(self, pr_number: int):
        raise ValueError("bad file view state adapter")


@pytest.mark.asyncio
async def test_load_pr_summary_propagates_non_runtime_adapter_errors() -> None:
    store = PRStore(pr_number=123)
    store._service = BrokenSummaryAdapter()  # type: ignore[assignment]

    with pytest.raises(ValueError, match="bad summary adapter state"):
        await store.load_pr_summary()


@pytest.mark.asyncio
async def test_load_files_propagates_non_runtime_adapter_errors() -> None:
    store = PRStore(pr_number=123)
    store._service = BrokenFilePageAdapter()  # type: ignore[assignment]

    with pytest.raises(ValueError, match="bad file page adapter state"):
        await store.load_files()


@pytest.mark.asyncio
async def test_load_file_view_states_propagates_non_runtime_adapter_errors() -> None:
    store = PRStore(pr_number=123)
    store._service = BrokenFileViewStateAdapter()  # type: ignore[assignment]

    with pytest.raises(ValueError, match="bad file view state adapter"):
        await store.load_file_view_states()
