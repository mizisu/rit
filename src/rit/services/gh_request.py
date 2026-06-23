from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = (
    "GitHubInputRequest",
    "GitHubInputRunner",
    "run_input_request",
    "run_request",
)


@dataclass(frozen=True)
class GitHubInputRequest:
    """A gh api request that sends JSON through stdin."""

    args: tuple[str, ...]
    input_text: str


class GitHubInputRunner(Protocol):
    """Runs a gh command with optional stdin text."""

    def __call__(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> Awaitable[str]: ...


async def run_input_request(
    request: GitHubInputRequest,
    runner: GitHubInputRunner,
) -> str:
    """Run a gh request that passes JSON through stdin."""
    return await runner(list(request.args), input_text=request.input_text)


async def run_request(
    args: Sequence[str],
    runner: GitHubInputRunner,
) -> str:
    """Run a gh request with sequence args."""
    return await runner(list(args))
