import asyncio
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
import queue
import threading
import urllib.request


__all__ = (
    "async_iter_pr_diff_sections",
    "async_iter_url_diff_sections",
    "fetch_pr_diff_text",
    "fetch_url_text",
    "iter_diff_sections",
    "iter_url_diff_sections",
    "pr_diff_url",
)


def pr_diff_url(repo_full_name: str, pr_number: int) -> str:
    """Return GitHub's raw web diff URL for a pull request."""
    return f"https://github.com/{repo_full_name}/pull/{pr_number}.diff"


def fetch_url_text(url: str) -> str:
    """Fetch text from a URL using rit's GitHub-friendly user agent."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "rit"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


async def fetch_pr_diff_text(
    repo_full_name: str,
    pr_number: int,
    *,
    text_fetcher: Callable[[str], str] = fetch_url_text,
) -> str:
    """Fetch the full raw PR diff text from GitHub's web diff endpoint."""
    return await asyncio.to_thread(
        text_fetcher,
        pr_diff_url(repo_full_name, pr_number),
    )


def iter_url_diff_sections(url: str) -> Iterator[str]:
    """Stream raw diff sections from a URL."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "rit"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        yield from iter_diff_sections(
            raw_line.decode("utf-8", errors="replace") for raw_line in response
        )


async def async_iter_url_diff_sections(
    url: str,
    *,
    section_reader: Callable[[str], Iterator[str]] = iter_url_diff_sections,
) -> AsyncIterator[str]:
    """Bridge blocking URL diff section streaming into an async iterator."""
    items: queue.Queue[str | Exception | KeyboardInterrupt | SystemExit | None] = (
        queue.Queue(maxsize=20)
    )

    def read_sections() -> None:
        try:
            for section in section_reader(url):
                items.put(section)
        except Exception as error:
            items.put(error)
        except (KeyboardInterrupt, SystemExit) as error:
            items.put(error)
        finally:
            items.put(None)

    thread = threading.Thread(
        target=read_sections,
        name="rit-pr-diff-stream",
        daemon=True,
    )
    thread.start()

    while True:
        item = await asyncio.to_thread(items.get)
        if item is None:
            break
        if isinstance(item, (Exception, KeyboardInterrupt, SystemExit)):
            raise item
        yield item


async def async_iter_pr_diff_sections(
    repo_full_name: str,
    pr_number: int,
    *,
    section_reader: Callable[[str], Iterator[str]] = iter_url_diff_sections,
) -> AsyncIterator[str]:
    """Stream raw PR diff sections from GitHub's web diff endpoint."""
    async for section in async_iter_url_diff_sections(
        pr_diff_url(repo_full_name, pr_number),
        section_reader=section_reader,
    ):
        yield section


def iter_diff_sections(lines: Iterable[str]) -> Iterator[str]:
    """Yield one raw diff section at a time from decoded diff lines."""
    current_section: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current_section:
            yield "".join(current_section).rstrip("\n")
            current_section = [line]
        else:
            current_section.append(line)

    if current_section:
        yield "".join(current_section).rstrip("\n")
