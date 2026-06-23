import importlib


EXPECTED_STATE_HELPER_EXPORTS = {
    "rit.state.discussion_signature": {
        "discussion_render_signature",
        "normalized_author_login",
        "thread_render_signature",
    },
    "rit.state.file_content": {
        "FileContentFetcher",
        "load_cached_file_content",
    },
    "rit.state.file_projection": {
        "diff_from_file_patch",
        "file_from_diff",
        "file_from_summary",
        "parse_file_patch_summaries",
    },
    "rit.state.issue_comments": {
        "IssueCommentSubmissionProjection",
        "apply_submitted_issue_comment",
        "insert_issue_comment",
        "normalize_issue_comment_body",
    },
    "rit.state.pr_management": {
        "AssigneeSelectionPlan",
        "ReviewerSelectionPlan",
        "plan_assignee_selection",
        "plan_reviewer_selection",
    },
    "rit.state.pr_merge": {
        "merge_pr_discussion",
        "merge_pr_summary",
    },
    "rit.state.reviewer_status": {
        "ReviewerDisplayState",
        "ReviewerKind",
        "derive_reviewer_states",
    },
    "rit.state.settings": {
        "Settings",
    },
    "rit.state.settings_schema": {
        "FieldType",
        "SCHEMA",
        "get_default_settings",
        "get_flat_defaults",
    },
}


def test_small_state_helpers_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_STATE_HELPER_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)
