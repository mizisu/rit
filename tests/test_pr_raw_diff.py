from collections.abc import Iterator
import inspect

import pytest

import rit.services.pr_raw_diff as pr_raw_diff_module
from rit.services.pr_raw_diff import (
    async_iter_pr_diff_sections,
    async_iter_url_diff_sections,
    fetch_pr_diff_text,
    fetch_url_text,
    iter_diff_sections,
    iter_url_diff_sections,
    pr_diff_url,
)


def test_pr_raw_diff_thread_bridge_does_not_catch_base_exception_directly() -> None:
    source = inspect.getsource(pr_raw_diff_module)

    assert "except BaseException" not in source


def test_pr_diff_url_targets_github_web_diff_endpoint() -> None:
    assert (
        pr_diff_url("owner/repo", 123)
        == "https://github.com/owner/repo/pull/123.diff"
    )


def test_fetch_url_text_sends_rit_user_agent_and_decodes_replacement(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"ok\xff"

    def urlopen(request, timeout: int) -> Response:
        captured["url"] = request.full_url
        captured["user_agent"] = request.headers["User-agent"]
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    text = fetch_url_text("https://example.test/diff")

    assert text == "ok\ufffd"
    assert captured == {
        "url": "https://example.test/diff",
        "user_agent": "rit",
        "timeout": 120,
    }


@pytest.mark.asyncio
async def test_fetch_pr_diff_text_builds_url_and_offloads_text_fetch() -> None:
    calls: list[str] = []

    def text_fetcher(url: str) -> str:
        calls.append(url)
        return "diff text"

    text = await fetch_pr_diff_text(
        "owner/repo",
        123,
        text_fetcher=text_fetcher,
    )

    assert text == "diff text"
    assert calls == ["https://github.com/owner/repo/pull/123.diff"]


def test_iter_diff_sections_splits_on_diff_headers() -> None:
    sections = list(
        iter_diff_sections(
            [
                "diff --git a/a.py b/a.py\n",
                "@@ -1 +1 @@\n",
                "-old\n",
                "+new\n",
                "diff --git a/b.py b/b.py\n",
                "@@ -2 +2 @@\n",
                "+second\n",
            ]
        )
    )

    assert sections == [
        "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new",
        "diff --git a/b.py b/b.py\n@@ -2 +2 @@\n+second",
    ]


def test_iter_url_diff_sections_decodes_streamed_bytes(monkeypatch) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> Iterator[bytes]:
            return iter(
                [
                    b"diff --git a/a.py b/a.py\n",
                    b"+ok\xff\n",
                    b"diff --git a/b.py b/b.py\n",
                    b"+next\n",
                ]
            )

    def urlopen(request, timeout: int) -> Response:
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert list(iter_url_diff_sections("https://example.test/diff")) == [
        "diff --git a/a.py b/a.py\n+ok\ufffd",
        "diff --git a/b.py b/b.py\n+next",
    ]


@pytest.mark.asyncio
async def test_async_iter_url_diff_sections_yields_sections_from_reader() -> None:
    calls: list[str] = []

    def section_reader(url: str) -> Iterator[str]:
        calls.append(url)
        yield "section 1"
        yield "section 2"

    sections = [
        section
        async for section in async_iter_url_diff_sections(
            "https://example.test/diff",
            section_reader=section_reader,
        )
    ]

    assert calls == ["https://example.test/diff"]
    assert sections == ["section 1", "section 2"]


@pytest.mark.asyncio
async def test_async_iter_pr_diff_sections_builds_url_and_yields_sections() -> None:
    calls: list[str] = []

    def section_reader(url: str) -> Iterator[str]:
        calls.append(url)
        yield "section 1"
        yield "section 2"

    sections = [
        section
        async for section in async_iter_pr_diff_sections(
            "owner/repo",
            123,
            section_reader=section_reader,
        )
    ]

    assert calls == ["https://github.com/owner/repo/pull/123.diff"]
    assert sections == ["section 1", "section 2"]


@pytest.mark.asyncio
async def test_async_iter_url_diff_sections_preserves_reader_os_errors() -> None:
    def section_reader(url: str) -> Iterator[str]:
        raise OSError("network down")
        yield url

    with pytest.raises(OSError, match="network down"):
        async for _section in async_iter_url_diff_sections(
            "https://example.test/diff",
            section_reader=section_reader,
        ):
            pass


@pytest.mark.asyncio
async def test_async_iter_url_diff_sections_preserves_reader_system_exit() -> None:
    def section_reader(url: str) -> Iterator[str]:
        raise SystemExit("stop stream")
        yield url

    with pytest.raises(SystemExit, match="stop stream"):
        async for _section in async_iter_url_diff_sections(
            "https://example.test/diff",
            section_reader=section_reader,
        ):
            pass
