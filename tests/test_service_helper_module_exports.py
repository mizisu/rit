import importlib


EXPECTED_SERVICE_HELPER_EXPORTS = {
    "rit.services.gh_cli": {
        "GhCliError",
        "gh_command",
        "gh_failure_message",
        "gh_missing_cli_message",
        "run_gh",
        "run_gh_sync",
    },
    "rit.services.gh_paginated_json": {
        "parse_paginated_items",
    },
    "rit.services.gh_request": {
        "GitHubInputRequest",
        "GitHubInputRunner",
        "run_input_request",
        "run_request",
    },
    "rit.services.github_repo": {
        "GitHubRepo",
        "fetch_repo_view",
        "parse_repo_view_response",
        "repo_view_request",
    },
    "rit.services.pr_review_comment_selection": {
        "ReviewCommentSide",
        "ReviewCommentTarget",
        "review_comment_target",
        "select_created_review_comment",
    },
    "rit.services.pr_review_comment_threads": {
        "review_threads_from_rest_comments",
    },
    "rit.services.pr_review_payload": {
        "pending_review_payload",
        "review_event_payload",
        "submit_review_payload",
    },
}


def test_small_service_helpers_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_SERVICE_HELPER_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)
