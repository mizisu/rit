import asyncio
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from rit.services import github, local_git
from rit.services.local_git import LocalGitSource
from rit.state.file_content import load_cached_file_content


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str, Callable[..., str]]:
    if shutil.which("git") is None:
        pytest.skip("Git is not installed")
    supported = subprocess.run(
        ["git", "--no-lazy-fetch", "--version"], capture_output=True, check=False
    )
    if supported.returncode:
        pytest.skip("Git lacks --no-lazy-fetch; local reads safely fall back")
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str, data: bytes | None = None) -> str:
        return (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    *args,
                ],
                input=data,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

    git("init", "-q")
    entries = []
    for path, content in [
        ("source.py", b"original\r\n"),
        ("empty.py", b""),
        ("한 글\t\n.py", "한글\n".encode()),
        ("binary", b"a\0b"),
        ("invalid", b"\xff"),
        ("large", b"x" * 100),
    ]:
        oid = git("hash-object", "-w", "--stdin", data=content)
        entries.append(f"100644 blob {oid}\t{path}".encode())
    tree = git("mktree", "-z", data=b"\0".join(entries) + b"\0")
    ref = git("commit-tree", tree, "-m", "test objects")
    return root, ref, git


async def test_local_source_cache_uses_immutable_objects_and_shared_fallback(
    repository: tuple[Path, str, Callable[..., str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, ref, git = repository
    (root / "source.py").write_text("dirty working copy")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {root / '.git'}\n")
    nested = linked / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    source = LocalGitSource(Path("."))
    empty_tree = git("mktree", data=b"")
    other = git("commit-tree", empty_tree, "-m", "replacement")
    git("replace", ref, other)

    calls = []

    async def remote(
        owner: str, repo: str, path: str, *, ref: str, **kwargs: object
    ) -> str:
        calls.append((path, ref))
        return "remote"

    service = github.GitHubService(owner="owner", repo="repo")
    monkeypatch.setattr(github, "fetch_file_content", remote)
    cache: dict[tuple[str, str], str] = {}
    for path, expected in [
        ("source.py", "original\r\n"),
        ("empty.py", ""),
        ("한 글\t\n.py", "한글\n"),
    ]:
        assert (
            await load_cached_file_content(
                cache, filename=path, ref=ref, fetch=service.get_file_content
            )
            == expected
        )
    assert calls == []
    monkeypatch.setattr(LocalGitSource, "_MAX_BLOB_BYTES", 16)
    for path in ("missing.py", "binary", "invalid", "large"):
        assert (
            await load_cached_file_content(
                cache, filename=path, ref=ref, fetch=service.get_file_content
            )
            == "remote"
        )
    assert [path for path, _ in calls] == ["missing.py", "binary", "invalid", "large"]
    assert await source.read_file("source.py", "f" * 40) is None
    monkeypatch.chdir(tmp_path)
    assert await source.read_file("source.py", ref) == "original\r\n"
    assert await service.get_file_content("source.py", ref) == "original\r\n"
    assert await LocalGitSource(Path(".")).read_file("source.py", ref) is None

    async def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("cache hit must not start Git")

    monkeypatch.setattr(local_git.asyncio, "create_subprocess_exec", unexpected)
    assert (
        await load_cached_file_content(
            cache, filename="source.py", ref=ref, fetch=service.get_file_content
        )
        == "original\r\n"
    )
    assert (root / "source.py").read_text() == "dirty working copy"


async def test_local_reader_rejects_mutable_refs_and_ambiguous_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid input must not start Git")

    monkeypatch.setattr(local_git.asyncio, "create_subprocess_exec", unexpected)
    source = LocalGitSource(Path.cwd())
    for ref in ("", "HEAD", "main", "deadbeef", "a" * 40 + ":other"):
        assert await source.read_file("source.py", ref) is None
    for path in (
        "",
        "/source.py",
        "../source.py",
        "./source.py",
        "a/../source.py",
        "a\0b",
    ):
        assert await source.read_file(path, "a" * 40) is None


async def test_missing_git_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    async def remote(
        owner: str, repo: str, path: str, *, ref: str, **kwargs: object
    ) -> str:
        return "remote"

    service = github.GitHubService(owner="owner", repo="repo")
    monkeypatch.setattr(github, "fetch_file_content", remote)
    monkeypatch.setattr(local_git.asyncio, "create_subprocess_exec", missing)
    assert (
        await load_cached_file_content(
            {}, filename="source.py", ref="a" * 40, fetch=service.get_file_content
        )
        == "remote"
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_slow_git_is_reaped(
    cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    class Process:
        returncode: int | None = None

        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.reaped = False

        def kill(self) -> None:
            self.returncode = -9
            self.stdout.feed_eof()

        async def communicate(self) -> tuple[bytes, bytes]:
            self.reaped = True
            return b"", b""

    process = Process()

    async def create(*args: str, **kwargs: object) -> Process:
        assert "--no-lazy-fetch" in args and "--no-replace-objects" in args
        started.set()
        return process

    monkeypatch.setattr(local_git.asyncio, "create_subprocess_exec", create)
    source = LocalGitSource(Path.cwd())
    monkeypatch.setattr(source, "_READ_TIMEOUT", 10 if cancel else 0.01)
    task = asyncio.create_task(source.read_file("source.py", "a" * 40))
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is None
    assert process.returncode == -9
    assert process.reaped
