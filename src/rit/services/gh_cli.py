from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import Sequence
from contextlib import suppress

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


def gh_failure_message(args: Sequence[str], stderr: str, stdout: str = "") -> str:
    """Include GitHub's actionable API validation details when available."""
    fallback = stderr.strip() or f"gh command failed: {' '.join(args)}"
    if not args or args[0] != "api":
        return fallback
    try:
        payload = json.loads(stdout)
    except ValueError, TypeError:
        return fallback
    if not isinstance(payload, dict):
        return fallback
    details: list[str] = []
    errors = payload.get("errors", [])
    if isinstance(errors, list):
        for error in errors:
            message = error.get("message") if isinstance(error, dict) else error
            if isinstance(message, str) and message.strip():
                details.append(message.strip())
    if not details:
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            details.append(message.strip())
    if not details:
        return fallback
    message = "; ".join(dict.fromkeys(details))
    status = re.search(r"\bHTTP \d{3}\b", stderr)
    if status and status[0] not in message:
        message += f" ({status[0]})"
    return message


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

    try:
        stdout, stderr = await proc.communicate(
            input_text.encode() if input_text is not None else None
        )
    except asyncio.CancelledError:
        await _terminate_process(proc)
        raise

    if proc.returncode != 0:
        raise GhCliError(gh_failure_message(args, stderr.decode(), stdout.decode()))

    return stdout.decode()


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=1)
    except TimeoutError:
        with suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


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
        raise GhCliError(
            gh_failure_message(args, error.stderr, error.stdout or "")
        ) from error
    except FileNotFoundError as error:
        raise GhCliError(gh_missing_cli_message()) from error
