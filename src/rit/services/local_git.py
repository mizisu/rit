"""Read immutable source text without changing or fetching repository objects."""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from pathlib import Path


class LocalGitSource:
    """Read immutable blobs from a fixed local repository location."""

    _MAX_BLOB_BYTES = 4 * 1024 * 1024
    _READ_TIMEOUT = 1.0

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path.absolute()

    async def read_file(self, path: str, ref: str) -> str | None:
        """Return a local UTF-8 blob, or None so the caller can use GitHub."""
        if (
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", ref) is None
            or "\0" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            return None

        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(self._READ_TIMEOUT):
                process = await asyncio.create_subprocess_exec(
                    "git",
                    "--no-lazy-fetch",
                    "--no-replace-objects",
                    "--no-optional-locks",
                    "cat-file",
                    "blob",
                    f"{ref}:{path}",
                    cwd=self._repo_path,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                assert process.stdout is not None
                try:
                    await process.stdout.readexactly(self._MAX_BLOB_BYTES + 1)
                except asyncio.IncompleteReadError as error:
                    content = error.partial
                else:
                    return None
                if await process.wait() != 0 or b"\0" in content:
                    return None
                return content.decode("utf-8")
        except (OSError, TimeoutError, UnicodeError):
            return None
        finally:
            if process is not None:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                await process.communicate()
