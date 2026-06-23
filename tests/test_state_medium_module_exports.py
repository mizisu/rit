import importlib


EXPECTED_STATE_MEDIUM_EXPORTS = {
    "rit.state.discussion_projection": {
        "DiscussionProjection",
        "RecentDiscussion",
        "ThreadResolutionProjection",
        "merge_recent_submitted_discussion",
        "project_discussion_state",
        "remember_submitted_comment",
        "remember_submitted_review",
        "thread_from_submitted_comment",
        "update_thread_resolution",
    },
    "rit.state.file_collection": {
        "FileAppendResult",
        "FileSelection",
        "append_file",
        "apply_file_summary",
        "apply_file_view_state",
        "apply_file_view_states",
        "apply_parsed_file",
        "cache_file_diff",
        "find_file",
        "load_file_diff",
        "select_file",
        "sync_file_comments",
    },
    "rit.state.file_ingest": {
        "DiffSectionStreamer",
        "DiffSummaryParser",
        "MutableFileIngestState",
        "PRFilePageGetter",
        "PRFilePagesGetter",
        "RawDiffTextGetter",
        "append_file_batch",
        "append_file_summaries",
        "append_parsed_files",
        "begin_file_ingest",
        "file_page_progress",
        "load_raw_diff_text",
        "load_rest_file_pages",
        "load_streamed_diff_summaries",
    },
    "rit.state.pending_review": {
        "InlineCommentSubmissionPlan",
        "PendingCommentDeleteResult",
        "PendingCommentSaveResult",
        "PendingCommentSide",
        "PendingReviewApplication",
        "PendingReviewClearResult",
        "PendingReviewProjection",
        "PendingReviewRestoration",
        "PendingReviewSnapshot",
        "PendingReviewSyncApplication",
        "PendingReviewSyncPlan",
        "PendingReviewSyncResult",
        "ReviewCommentsLoader",
        "ReviewSubmissionEvent",
        "ReviewSubmissionPlan",
        "UnsupportedInlineCommentTarget",
        "apply_pending_review_projection",
        "apply_pending_review_sync_result",
        "clear_pending_review",
        "delete_pending_comment",
        "first_unsupported_comment",
        "get_pending_file_comments",
        "get_pending_inline_comment",
        "is_inline_comment_diff_line",
        "load_pending_review_projection",
        "plan_inline_comment_submission",
        "plan_pending_review_sync",
        "plan_review_submission",
        "project_pending_review",
        "project_pending_review_sync_result",
        "remove_pending_comment",
        "require_inline_comment_diff_line",
        "restore_pending_review_snapshot",
        "save_pending_comment",
        "select_pending_review",
        "should_restore_pending_review_snapshot",
        "snapshot_pending_review",
        "syncable_comments",
        "upsert_pending_comment",
    },
}


def test_medium_state_helpers_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_STATE_MEDIUM_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)
