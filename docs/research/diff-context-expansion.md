# Diff context expansion research and implementation plan

## Goal

Add LazyGit-style context controls to the normal Files diff:

- `Ctrl++`: increase context by one line
- `Ctrl+-`: decrease context by one line
- initial value `3`, minimum `0`, no artificial maximum
- one session-wide value retained across Files refreshes
- bindings work only while `DiffView` is focused and stay hidden from the footer
- show `Diff context: N lines` immediately

LazyGit likewise changes a global diff-context integer one line at a time, saturates at zero, and rerenders the current diff ([controller source](https://github.com/jesseduffield/lazygit/blob/master/pkg/gui/controllers/context_lines_controller.go), [configuration docs](https://github.com/jesseduffield/lazygit/blob/master/docs/Config.md)).

## Locked behavior

- The continuous `All files` document uses one context value for every file.
- Context generation runs outside the Textual UI thread and only the latest queued value may publish a render.
- Increasing context naturally merges hunks when their context regions meet.
- Preserve the semantic cursor `(path, side, line)`, column, viewport offset, and file folds. If a reduced context removes the cursor line, move to the nearest changed line in the same file.
- Reset visual selection and search when the diff is rebuilt.
- Binary, truncated, or otherwise unavailable text silently keeps its canonical diff.
- Full-file preview and timeline hunk previews are unaffected.
- Expanded-only lines look like normal context, but starting a line/range comment on them shows a warning and does not open the editor.
- Renamed files must use the old path at the base SHA and the new path at the head SHA.

## Current architecture findings

### Complete combined documents are required

`build_combined_diff_document()` returns no document until every file has a `FileDiff`, then assigns global line indexes and builds `(path, line, side)` lookups across the complete source (`src/rit/ui/components/combined_diff.py`). An earlier file gaining context shifts every later file's indexes.

`DiffView` virtualization limits mounted widgets only after the complete source has been flattened and indexed (`src/rit/ui/widgets/diff_view.py`, `src/rit/ui/widgets/diff_plan.py`, `src/rit/ui/widgets/diff_virtual.py`). It does not make source construction file- or viewport-lazy.

Therefore true viewport-lazy context would require placeholders, estimated heights, repeated global reindexing, and scroll correction. That is a new document model rather than an optimization of the existing one.

### Existing coalescing is sufficient

`FilesRenderSession` already keeps one queued combined-render request, `FileChanges` drops obsolete intermediate work, and `DiffView` has a render token (`src/rit/ui/components/files_render_session.py`, `src/rit/ui/components/file_changes.py`, `src/rit/ui/widgets/diff_view.py`). Context and a generation number can extend these seams; no new scheduler is needed.

### Full text is currently downloaded and discarded

The GraphQL file loader fetches base/head blobs and calls `difflib.unified_diff()` with its default three context lines, then retains only `PRFile.patch` (`src/rit/services/pr_file_request.py`, `src/rit/state/models.py`). Parsed `FileDiff` objects contain only lines present in that patch (`src/rit/core/diff.py`, `src/rit/core/types.py`), so context beyond three lines cannot be recovered without retaining source text.

### Canonical diff must remain separate

`PRStoreState.file_diffs` is also the authority used to validate GitHub inline-comment targets (`src/rit/state/store.py`, `src/rit/state/pending_review.py`). Replacing it with expanded display variants would incorrectly mark local context as GitHub-commentable.

A live control experiment on temporary draft PR [mizisu/rit#3](https://github.com/mizisu/rit/pull/3) confirmed the boundary:

- an unchanged line inside GitHub's canonical three-line patch context was accepted by the review-comment API and immediately deleted;
- the next unchanged line, visible only with larger client context, returned HTTP 422;
- the temporary comment was deleted, the branch was deleted, and the PR was closed.

The endpoint contract is documented by GitHub's [pull request review comments API](https://docs.github.com/en/rest/pulls/comments).

### Rename handling has a pre-existing fallback gap

The GraphQL changed-file query receives the new path and `RENAMED` status but not the old path. Initial base lookup consequently uses the new path. Streamed raw-diff metadata later supplies `previous_filename`, but replacement currently does not hydrate old-path base text or invalidate an already parsed stale diff (`src/rit/services/pr_file_request.py`, `src/rit/state/file_collection.py`, `src/rit/state/file_workspace.py`). Dynamic context needs this corrected at ingestion.

## Performance research

Four strategies were compared with Python 3.14 synthetic sparse/dense diffs and randomized shape checks. CPython's implementation shows that `unified_diff()` creates a `SequenceMatcher`, while context grouping operates on opcode groups ([CPython `difflib.py`](https://github.com/python/cpython/blob/3.14/Lib/difflib.py)).

| Strategy | Result |
|---|---|
| Re-run `unified_diff` and parse for every N | Smallest initial change, but repeats whole-file matching and parsing |
| Immutable opcodes, then patch and parse | Faster, but still allocates patch text and reparses it; alignment can differ from GitHub's canonical diff |
| Prebuild a full aligned `FileDiff` | Lowest isolated projection time, but retained roughly 2–4 MiB per measured 3k–6k-line file before the unavoidable combined copy/index |
| Canonical change spine projected directly into the combined document | Best practical latency, tiny retained metadata, and preserves GitHub's chosen change alignment |

Measured direct canonical-spine projection for an eight-file combined document:

| Data shape | N=0 | N=3 | N=10 | Full context |
|---|---:|---:|---:|---:|
| Sparse | 4.58 ms | 9.70 ms | 11.47 ms | 69.85 ms |
| Moderately dense | 11.18 ms | 17.70 ms | 26.68 ms | 38.28 ms |

The corresponding two-stage per-file variant plus combined-copy path reached 176 ms at full context. These are directional local measurements, not a CI timing SLA. Randomized checks covered 2,400 generated `FileDiff` shapes and matched expected hunk ranges, coordinates, change flags, and word segments.

## Decision

Use **canonical change-spine projection fused with combined-document construction**.

For each file, extract and validate an immutable description of changed runs once from the canonical `FileDiff`. For context `N`:

1. group adjacent runs when the unchanged gap is at most `2 * N`;
2. read only the required equal lines from retained base/head text;
3. shallow-copy changed `DiffLine` values so mutable `line_index` is never shared, while reusing existing word-diff segment data;
4. append hunks and global lookup entries directly to the new `CombinedDiffDocument`;
5. skip the intermediate per-file display `FileDiff` mapping and patch serialization/parsing.

For `N=3`, reuse the canonical path exactly. If retained text or spine validation is unavailable, use that file's canonical diff silently. Keep only the active combined document; do not cache unbounded variants for an unbounded N.

True viewport/file-level lazy context is explicitly deferred. It adds a cross-cutting partial-document state machine while providing little benefit after direct projection reduces generation to tens of milliseconds.

## Implementation sequence

1. **Retain context inputs**
   - Preserve usable base/head text and ref identity as transient, non-serialized PR-file data during `src/rit/services/pr_file_request.py` loading.
   - Clear source/spine caches on a new file ingest while leaving the session context value intact.

2. **Correct rename reconciliation**
   - Preserve the already fetched new-path head text.
   - After raw-diff metadata provides `previous_filename`, hydrate missing base text from `previous_filename@base_sha`.
   - Invalidate stale parsed canonical/spine state when raw metadata replaces a patch.

3. **Add the pure projector**
   - Extract validated changed-run spines from canonical diffs.
   - Project contexts `0..N`, clamp at file boundaries, and merge hunks at the standard `2*N` boundary.
   - Fuse projection into `src/rit/ui/components/combined_diff.py` so lines are copied/indexed once.

4. **Wire focused controls**
   - Add hidden `ctrl+plus` and `ctrl+minus` bindings to `DiffView`; add no compatibility aliases.
   - Emit a delta request to `FileChanges`, which owns the session value and immediate flash.
   - Ignore the request during full-file preview.

5. **Extend existing render coordination**
   - Include context and content generation in `FilesRenderSession` render identity.
   - Snapshot source inputs and build the complete projected document in one `asyncio.to_thread()` call.
   - Keep one pending request; reject stale worker results before recording or calling `show_diff()`.

6. **Restore navigation state**
   - Capture path, side, line, pane, column, viewport offset, focus, and fold state before queueing.
   - Restore through existing semantic line lookup and jump helpers; fall back to the nearest changed line in the same file.

7. **Enforce comment boundaries early**
   - Before opening a new inline/range editor, validate endpoints against canonical `state.file_diffs`.
   - Warn and return for expanded-only targets. Existing comments and file-level comments remain unchanged.

## Test plan

- `tests/test_combined_diff.py`: contexts 0/1/3/large, file boundaries, exact hunk split/merge threshold, global indexes, canonical fallback, randomized spine equivalence.
- `tests/test_pr_file_request.py`: retained text/ref data, binary/truncated handling, rename old-path hydration.
- `tests/test_file_collection.py`: raw-summary replacement invalidates stale canonical/spine state without losing retained head text.
- `tests/test_files_render_session.py`: context participates in identity and rapid requests publish only the latest generation.
- `tests/test_file_changes.py`: focused bindings, floor zero, immediate flash, full-preview no-op, semantic cursor/viewport/fold restoration, and search/selection reset.
- Existing inline-comment tests: canonical lines remain commentable; expanded-only single/range targets are blocked with the warning.
- Structural performance coverage: no network request on normal keypress, projection runs off the UI thread, no patch reparse/per-context `SequenceMatcher`, and stale work cannot invoke `show_diff()`.
- Final behavioral validation: `uv run pytest -q tests`.

## Deferred work

- viewport-aware partial combined documents;
- persistent context settings;
- compatibility aliases such as `Ctrl+=` or `Ctrl+_`;
- moving `DiffView` plan construction off-thread unless profiling identifies it as the next bottleneck;
- fixed millisecond assertions in CI.
