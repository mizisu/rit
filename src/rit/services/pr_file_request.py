import json

from pydantic import TypeAdapter

from rit.services.gh_request import GitHubInputRunner, run_request
from rit.services.pr_file_pagination import (
    PR_FILES_PAGE_CONCURRENCY,
    PR_FILES_PER_PAGE,
    collect_all_page_items,
    collect_page_batches,
)
from rit.state.models import PRFile


__all__ = (
    "fetch_file_content",
    "fetch_pr_file_pages",
    "fetch_pr_files",
    "fetch_pr_files_page",
    "file_content_request",
    "parse_pr_files_page",
    "parse_pr_files_result",
    "pr_files_page_request",
)


_PRFileListAdapter: TypeAdapter[list[PRFile]] = TypeAdapter(list[PRFile])


def pr_files_page_request(
    repo_full_name: str,
    pr_number: int,
    *,
    page: int,
    per_page: int,
) -> tuple[str, ...]:
    """Build a gh api request for one PR files REST page."""
    return (
        "api",
        f"/repos/{repo_full_name}/pulls/{pr_number}/files?per_page={per_page}&page={page}",
    )


def parse_pr_files_page(data: object) -> list[PRFile]:
    """Normalize a PR files REST page into PRFile models."""
    if isinstance(data, list):
        return _PRFileListAdapter.validate_python(data)
    return _PRFileListAdapter.validate_python([data])


def parse_pr_files_result(result: str) -> list[PRFile]:
    """Parse a gh REST result for one PR files page."""
    return parse_pr_files_page(json.loads(result))


async def fetch_pr_files_page(
    repo_full_name: str,
    pr_number: int,
    *,
    page: int,
    per_page: int,
    runner: GitHubInputRunner,
) -> list[PRFile]:
    """Fetch and parse one PR files REST page."""
    return parse_pr_files_result(
        await run_request(
            pr_files_page_request(
                repo_full_name,
                pr_number,
                page=page,
                per_page=per_page,
            ),
            runner,
        )
    )


async def fetch_pr_file_pages(
    repo_full_name: str,
    pr_number: int,
    *,
    pages: tuple[int, ...],
    per_page: int = PR_FILES_PER_PAGE,
    concurrency: int = PR_FILES_PAGE_CONCURRENCY,
    runner: GitHubInputRunner,
) -> dict[int, list[PRFile]]:
    """Fetch multiple PR file REST pages concurrently by page number."""
    return await collect_page_batches(
        pages,
        lambda page: fetch_pr_files_page(
            repo_full_name,
            pr_number,
            page=page,
            per_page=per_page,
            runner=runner,
        ),
        concurrency=concurrency,
    )


async def fetch_pr_files(
    repo_full_name: str,
    pr_number: int,
    *,
    total_count: int | None = None,
    per_page: int = PR_FILES_PER_PAGE,
    concurrency: int = PR_FILES_PAGE_CONCURRENCY,
    runner: GitHubInputRunner,
) -> list[PRFile]:
    """Fetch PR files with a fast first page and concurrent remaining pages."""
    first_page = await fetch_pr_files_page(
        repo_full_name,
        pr_number,
        page=1,
        per_page=per_page,
        runner=runner,
    )

    async def fetch_pages(pages: tuple[int, ...]) -> dict[int, list[PRFile]]:
        return await fetch_pr_file_pages(
            repo_full_name,
            pr_number,
            pages=pages,
            per_page=per_page,
            concurrency=concurrency,
            runner=runner,
        )

    return list(
        await collect_all_page_items(
            first_page,
            fetch_pages,
            total_count_hint=total_count or 0,
            per_page=per_page,
        )
    )


def file_content_request(
    repo_full_name: str,
    path: str,
    *,
    ref: str,
) -> tuple[str, ...]:
    """Build a gh api request for raw file content at a ref."""
    return (
        "api",
        f"/repos/{repo_full_name}/contents/{path}?ref={ref}",
        "-H",
        "Accept: application/vnd.github.raw+json",
    )


async def fetch_file_content(
    repo_full_name: str,
    path: str,
    *,
    ref: str,
    runner: GitHubInputRunner,
) -> str:
    """Fetch raw file content at a Git ref."""
    return await run_request(
        file_content_request(repo_full_name, path, ref=ref),
        runner,
    )
