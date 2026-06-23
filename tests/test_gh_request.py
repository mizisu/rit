from rit.services.gh_request import GitHubInputRequest, run_input_request, run_request


async def test_run_request_passes_args_to_runner() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "ok"

    result = await run_request(
        ("api", "/repos/owner/repo/pulls/1"),
        runner,
    )

    assert result == "ok"
    assert calls == [(["api", "/repos/owner/repo/pulls/1"], None)]


async def test_run_input_request_passes_args_and_input_text_to_runner() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "ok"

    result = await run_input_request(
        GitHubInputRequest(
            args=("api", "--method", "POST", "/repos/owner/repo"),
            input_text='{"hello":"world"}',
        ),
        runner,
    )

    assert result == "ok"
    assert calls == [
        (
            ["api", "--method", "POST", "/repos/owner/repo"],
            '{"hello":"world"}',
        )
    ]
