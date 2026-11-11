from __future__ import annotations

import tomllib
from inspect import iscoroutinefunction
from pathlib import Path

from tests.conftest import wait_until


def _pytest_options() -> dict[str, object]:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    return pyproject["tool"]["pytest"]["ini_options"]


def test_pytest_runs_the_full_suite_in_parallel_by_default() -> None:
    addopts = _pytest_options()["addopts"]
    assert isinstance(addopts, list)
    assert "-n" in addopts
    assert "auto" in addopts


def test_pytest_parallelism_uses_workstealing_distribution() -> None:
    addopts = _pytest_options()["addopts"]
    assert isinstance(addopts, list)
    assert "--dist" in addopts
    assert "worksteal" in addopts


def test_wait_until_helper_exists() -> None:
    assert iscoroutinefunction(wait_until)
