from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Sequence

__all__ = (
    "GhCliError",
    "gh_command",
    "gh_failure_message",
    "gh_missing_cli_message",
    "run_gh",
    "run_gh_sync",
)


class GhCliError(RuntimeError):
    """Raised when the gh CLI command fails."""


def gh_command(args: Sequence[str]) -> tuple[str, ...]:
    """Return the full gh command for subprocess execution."""
    return ("gh", *args)


def gh_failure_message(args: Sequence[str], stderr: str) -> str:
    """Return the user-facing gh command failure message."""
    return stderr.strip() or f"gh command failed: {' '.join(args)}"


def gh_missing_cli_message() -> str:
    """Return the user-facing message for a missing gh binary."""
    return "gh CLI not found. Please install GitHub CLI: https://cli.github.com/"


async def run_gh(args: Sequence[str], *, input_text: str | None = None) -> str:
    """Run gh asynchronously and return stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *gh_command(args),
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise GhCliError(gh_missing_cli_message()) from error

    stdout, stderr = await proc.communicate(
        input_text.encode() if input_text is not None else None
    )

    if proc.returncode != 0:
        raise GhCliError(gh_failure_message(args, stderr.decode()))

    return stdout.decode()


def run_gh_sync(args: Sequence[str]) -> str:
    """Run gh synchronously and return stdout."""
    try:
        result = subprocess.run(
            gh_command(args),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as error:
        raise GhCliError(gh_failure_message(args, error.stderr)) from error
    except FileNotFoundError as error:
        raise GhCliError(gh_missing_cli_message()) from error
