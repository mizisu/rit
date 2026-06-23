import importlib
import pkgutil

import rit.state


EXPECTED_STATE_MODEL_EXPORTS = {
    "CommentThread",
    "FileViewedState",
    "LoadingState",
    "NodeList",
    "PR",
    "PRComment",
    "PRFile",
    "PRIssueComment",
    "PRLabel",
    "PRReview",
    "PRState",
    "PRTeam",
    "PRUser",
    "PendingReviewComment",
    "ReviewRequest",
    "ReviewState",
    "ReviewThread",
    "ReviewThreadInfo",
    "group_comments_into_threads",
}

EXPECTED_STATE_STORE_EXPORTS = {
    "PRStore",
    "PRStoreState",
    "UnsupportedInlineCommentTarget",
}


def test_state_models_export_documented_surface() -> None:
    module = importlib.import_module("rit.state.models")
    exports = tuple(module.__all__)

    assert set(exports) == EXPECTED_STATE_MODEL_EXPORTS
    assert len(exports) == len(set(exports))
    assert exports == tuple(sorted(exports))
    for name in exports:
        assert hasattr(module, name)


def test_state_store_exports_documented_surface() -> None:
    module = importlib.import_module("rit.state.store")
    exports = tuple(module.__all__)

    assert set(exports) == EXPECTED_STATE_STORE_EXPORTS
    assert len(exports) == len(set(exports))
    assert exports == tuple(sorted(exports))
    for name in exports:
        assert hasattr(module, name)


def test_every_state_module_defines_explicit_exports() -> None:
    missing_exports = [
        module_info.name
        for module_info in pkgutil.iter_modules(
            rit.state.__path__,
            prefix=f"{rit.state.__name__}.",
        )
        if not hasattr(importlib.import_module(module_info.name), "__all__")
    ]

    assert missing_exports == []
