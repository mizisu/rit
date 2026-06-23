import pytest


def _file_content_module():
    import rit.state.file_content as file_content

    return file_content


@pytest.mark.asyncio
async def test_load_cached_file_content_returns_cached_without_fetching() -> None:
    file_content = _file_content_module()
    cache = {"src/app.py": "cached content"}
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, ref: str) -> str:
        calls.append((path, ref))
        return "fresh content"

    result = await file_content.load_cached_file_content(
        cache,
        filename="src/app.py",
        head_sha="deadbeef",
        fetch=fetch,
    )

    assert result == "cached content"
    assert calls == []


@pytest.mark.asyncio
async def test_load_cached_file_content_requires_head_sha() -> None:
    file_content = _file_content_module()
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, ref: str) -> str:
        calls.append((path, ref))
        return "fresh content"

    result = await file_content.load_cached_file_content(
        {},
        filename="src/app.py",
        head_sha="",
        fetch=fetch,
    )

    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_load_cached_file_content_allows_missing_fetcher_for_cached_content() -> None:
    file_content = _file_content_module()

    result = await file_content.load_cached_file_content(
        {"src/app.py": "cached content"},
        filename="src/app.py",
        head_sha="deadbeef",
        fetch=None,
    )

    assert result == "cached content"


@pytest.mark.asyncio
async def test_load_cached_file_content_returns_none_when_fetcher_is_missing() -> None:
    file_content = _file_content_module()

    result = await file_content.load_cached_file_content(
        {},
        filename="src/app.py",
        head_sha="deadbeef",
        fetch=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_load_cached_file_content_fetches_and_caches_content() -> None:
    file_content = _file_content_module()
    cache: dict[str, str] = {}
    calls: list[tuple[str, str]] = []

    async def fetch(path: str, ref: str) -> str:
        calls.append((path, ref))
        return "fresh content"

    result = await file_content.load_cached_file_content(
        cache,
        filename="src/app.py",
        head_sha="deadbeef",
        fetch=fetch,
    )

    assert result == "fresh content"
    assert cache == {"src/app.py": "fresh content"}
    assert calls == [("src/app.py", "deadbeef")]


@pytest.mark.asyncio
async def test_load_cached_file_content_swallows_fetch_errors() -> None:
    file_content = _file_content_module()
    cache: dict[str, str] = {}

    async def fetch(path: str, ref: str) -> str:
        raise RuntimeError("boom")

    result = await file_content.load_cached_file_content(
        cache,
        filename="src/app.py",
        head_sha="deadbeef",
        fetch=fetch,
    )

    assert result is None
    assert cache == {}


@pytest.mark.asyncio
async def test_load_cached_file_content_reraises_non_runtime_fetch_errors() -> None:
    file_content = _file_content_module()
    cache: dict[str, str] = {}

    async def fetch(path: str, ref: str) -> str:
        raise ValueError("bad fetch adapter state")

    with pytest.raises(ValueError, match="bad fetch adapter state"):
        await file_content.load_cached_file_content(
            cache,
            filename="src/app.py",
            head_sha="deadbeef",
            fetch=fetch,
        )

    assert cache == {}
