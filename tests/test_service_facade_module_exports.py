import importlib
import pkgutil

import rit.services


EXPECTED_GITHUB_FACADE_EXPORTS = {
    "GitHubError",
    "GitHubRepo",
    "GitHubService",
    "PRDiscussion",
    "translate_pull_request_graphql_errors",
}


def test_github_facade_exports_documented_surface() -> None:
    module = importlib.import_module("rit.services.github")
    exports = tuple(module.__all__)

    assert set(exports) == EXPECTED_GITHUB_FACADE_EXPORTS
    assert len(exports) == len(set(exports))
    assert exports == tuple(sorted(exports))
    for name in exports:
        assert hasattr(module, name)


def test_every_service_module_defines_explicit_exports() -> None:
    missing_exports = [
        module_info.name
        for module_info in pkgutil.iter_modules(
            rit.services.__path__,
            prefix=f"{rit.services.__name__}.",
        )
        if not hasattr(importlib.import_module(module_info.name), "__all__")
    ]

    assert missing_exports == []
