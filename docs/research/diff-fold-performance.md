# Research: `DiffView` file fold/unfold performance

## Summary

`DiffView` currently treats a single file fold as a new diff: it builds a replacement `FileDiff`, resets view-wide indexes/caches, replans every visible line, removes every child under `#diff-content`, and remounts the rendered window. That makes the operation scale with the combined diff rather than the changed file and also destabilizes cursor, scroll, widget, and highlight identity.

The recommended design is to keep one canonical diff with stable line/hunk identities and represent folding as a per-file visibility projection. Use a hybrid renderer: retain small/medium file bodies and toggle `display: none`; for virtualized/large bodies, remove or mount only the target file's intersecting grouped blocks. Serialize and batch every structural mutation with `Widget.batch()`, await the removal/mount awaitables, then restore a logical viewport anchor with `call_after_refresh()`.

## Findings

1. **The hot path is an unconditional whole-view rebuild.** Manual folding in `toggle_current_file_fold()` and automatic folding after a viewed-state change both ultimately call `show_diff()`. `show_diff()` clears search/comment/row/geometry/widget state, rebuilds a `DiffPlan`, rebuilds comment and virtual-layout metadata, revisits highlighting, and invokes `_render_diff()`. `_render_diff()` calls `remove_children()` on the entire content container before rendering every non-virtual hunk or the whole current virtual window. The small helper that constructs folded placeholders is not the dominant problem; the global work after it is. [Repository `diff_view.py`](../../src/rit/ui/widgets/diff_view.py) (`_render_diff_for_source`, `toggle_current_file_fold`, `refresh_viewed_folds`, `_refresh_viewed_folds`, `show_diff`); [repository `diff_folding.py`](../../src/rit/ui/widgets/diff_folding.py); [repository `diff_render.py`](../../src/rit/ui/widgets/diff_render.py) (`_render_diff`).

2. **A “mark viewed” currently produces two visual phases.** `FileChanges.update_file_view_state()` first refreshes the tree/header, then `DiffView.refresh_viewed_folds()` schedules the fold worker with `call_after_refresh()`. The user may therefore see an expanded header change state in one frame and the full diff tree collapse in a later frame. `_refresh_viewed_folds()` does protect the subsequent rebuild with an outer `App.batch_update()` and waits for pending mounts, which reduces blank intermediate paint, but does not remove the expensive reset/replan/remount or the two-phase transition. [Repository `file_changes.py`](../../src/rit/ui/components/file_changes.py) (`update_file_view_state`); [repository `diff_view.py`](../../src/rit/ui/widgets/diff_view.py) (`refresh_viewed_folds`, `_refresh_viewed_folds`).

3. **Fold state is encoded by changing data identity and positional indexes.** `build_viewed_file_fold_diff()` replaces all hunks for a folded file with one synthetic zero-height line. `build_diff_plan()` then assigns `line.line_index` from the new visible order. Consequently, folding one early file shifts positional line and hunk indexes for every later file; preserving downstream widgets safely is difficult because widget IDs and registries are keyed by those indexes. In contrast, the combined document already computes canonical file starts and `(path, line, side)` lookups before folding. Preserve those canonical identities and add a separate visible-row/file-height projection rather than compacting the source model. [Repository `diff_folding.py`](../../src/rit/ui/widgets/diff_folding.py); [repository `diff_plan.py`](../../src/rit/ui/widgets/diff_plan.py) (`build_diff_plan`); [repository `combined_diff.py`](../../src/rit/ui/components/combined_diff.py) (`CombinedDiffDocument`, `build_combined_diff_document`).

4. **The repository already contains most low-level incremental machinery.** Medium diffs use hunk-sized `UnifiedDiffBlock`/`SplitDiffBlock` widgets; their line visuals support fixed-row refreshes. Large diffs maintain top/bottom spacers and incrementally remove, repair, and mount only changing virtual-window ranges. Tests already assert that small virtual shifts avoid `_render_diff()` and preserve surviving block identity. A fold delta should reuse these primitives at a file-segment boundary rather than introduce another renderer. [Repository `diff_blocks.py`](../../src/rit/ui/widgets/diff_blocks.py); [repository `diff_types.py`](../../src/rit/ui/widgets/diff_types.py); [repository `diff_virtual.py`](../../src/rit/ui/widgets/diff_virtual.py) (`_remove_virtualized_lines`, `_try_shift_virtual_window_incremental`, `_sync_virtual_buffers`); [repository performance tests](../../tests/test_diff_view_performance.py).

5. **Textual provides the correct atomic mutation primitive.** In Textual 8.2.8, `Widget.batch()` is an async context manager combining the widget lock with `App.batch_update()`; its official example awaits `remove_children()` and `mount()` in the same transaction. `App.batch_update()` alone only suspends repaint—it does not serialize competing workers or guarantee mount completion. `mount()` returns `AwaitMount`; removal returns `AwaitRemove`. Use one container-owned batch, await bulk removal/mounting inside it, and do not fire un-awaited structural mutations from competing fold and virtual-window workers. [Textual Widget API](https://textual.textualize.io/api/widget/); [tagged 8.2.8 `widget.py`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widget.py); [Textual App API](https://textual.textualize.io/api/app/); [tagged 8.2.8 `app.py`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py).

6. **Hide and remove have materially different costs and behavior.** `display: none` removes layout space while preserving the DOM subtree; `visibility: hidden` preserves layout space and therefore is not a fold mechanism. Textual layout/focus traversal uses displayed children, so a `display: none` file body is excluded from layout and the focus chain. However, it remains queryable and retains memory/state. Removal invokes pruning across descendants, resets focus if it lies in the pruned subtree, closes widget message loops, and requests `parent.refresh(layout=True)` after completion. This favors `display: none` for resident bodies and targeted removal for large/virtualized bodies. [Textual display](https://textual.textualize.io/styles/display/); [Textual visibility](https://textual.textualize.io/styles/visibility/); [tagged 8.2.8 `dom.py`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/dom.py) (`display`, `visible`, `displayed_children`); [tagged 8.2.8 `screen.py`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/screen.py) (`focus_chain`); [tagged 8.2.8 `app.py`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py) (`_prune`).

7. **Logical scroll restoration must happen after layout, not by restoring raw `scroll_y`.** Collapsing content above the viewport changes every later vertical coordinate. Capture an anchor such as `(path, old/new line number or file header, side, viewport row offset)`, apply the fold, then resolve that anchor against the new projection after refresh. If the anchor is inside the file being collapsed, resolve it to that file's header. Textual documents `call_after_refresh()` as running after queued messages and screen refresh; `DiffView` already has row/viewport-offset helpers and uses this scheduling pattern for virtual cursor reveal. [Textual MessagePump API](https://textual.textualize.io/api/message_pump/); [tagged 8.2.8 `message_pump.py`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/message_pump.py); [repository `diff_cursor.py`](../../src/rit/ui/widgets/diff_cursor.py) (`_current_cursor_viewport_offset`, `_jump_to_row_with_anchor`); [repository virtual tests](../../tests/test_diff_virtual.py).

8. **Keeping canonical diff identity also avoids unnecessary highlight churn.** Highlight cache keys contain `id(diff)`. Every non-empty folded projection is a new `FileDiff`, so the cache cannot hit even though most source lines are unchanged; the windowed path may clear shared highlighted content and schedule new range work. A canonical model lets a fold preserve highlight data and request only newly revealed rows. Existing request tokens and in-place grouped-block refreshes can reject stale background results without a full render. [Repository `diff_highlight.py`](../../src/rit/ui/widgets/diff_highlight.py) (`_highlight_cache_key`, `_highlight_diff_async`, `_highlight_diff_range_async`, `_refresh_rendered_highlight_range`); [repository `diff_view.py`](../../src/rit/ui/widgets/diff_view.py) (`show_diff`).

9. **A whole-view Line API renderer is the highest-ceiling option, but not the first change.** Textual's Line API redraws only requested rows; `ScrollView` is specifically a childless, self-scrolling Line API base. Textual 8.2.8's `OptionList` demonstrates the relevant architecture: logical items plus line/height/index caches, `virtual_size`, cached strips, and `render_line(y)` rather than a child widget per item. `rit` already applies this idea inside `DiffCode` and `LineAnnotations`, so a future whole-diff renderer could make folding a height/index-cache update with no code-line DOM churn. File headers, comment cards/editors, split-pane horizontal scrolling, mouse metadata, and text selection make this a substantial migration rather than a quick fix. [Textual Line API guide](https://textual.textualize.io/guide/widgets/#line-api); [Textual ScrollView API](https://textual.textualize.io/api/scroll_view/); [tagged 8.2.8 `scroll_view.py`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/scroll_view.py); [tagged 8.2.8 `OptionList`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widgets/_option_list.py); [repository `diff_visual.py`](../../src/rit/ui/widgets/diff_visual.py).

## Local measurements

A parent-side benchmark used Python 3.14.2, Textual 8.2.8, `App.run_test(size=(120, 30))`, and synthetic combined diffs. These numbers are directional developer measurements, not CI limits.

| Combined diff | Current fold refresh |
|---|---:|
| 2 files × 10 lines | 10–13 ms |
| 10 files × 10 lines | 69–80 ms |
| 20 files × 20 lines | 27–28 ms |
| 100 files × 20 lines | 17–21 ms |
| 100 files × 100 lines | 30–42 ms |
| 200 files × 100 lines | 53–57 ms |

The non-monotonic result identifies two costs. Below `BLOCK_RENDER_LINE_THRESHOLD = 120`, rebuilding mounts one widget per line; profiling the 10 × 10 case was dominated by mount, registration, stylesheet application, and per-line hunk rendering. Large virtualized cases mount fewer widgets, but still rebuild the complete plan and rendered-row/geometry tables, so cost grows with total diff size.

Two controlled experiments confirmed the optimization direction:

- For the 10 × 10 fixture, forcing the existing grouped renderer for the combined document reduced median fold refresh from **114.8 ms to 24.2 ms (4.7×)** while leaving the highlighting threshold unchanged.
- Hiding only the target file's ten already-mounted row widgets took roughly **0.06 ms synchronously** and reached the next test display in **24.5 ms**, versus **115.7 ms** for the full fold refresh. This was a visual-only experiment, not a valid implementation: navigation, comments, geometry, and folded projection were intentionally not updated.

Always enabling grouped blocks is not a complete patch because small-diff behavior exposes stable `#line-*` widgets and interaction tests rely on those identities. The experiment is evidence that mount churn is the small/medium-diff bottleneck, not a recommendation to bypass those contracts. The canonical per-file projection remains necessary for large diffs and correctness-preserving incremental updates.

## Ranked approaches

| Rank | Approach | Expected fold cost | Strengths | Main risks |
|---:|---|---|---|---|
| 1 | **Incremental hide/show of a retained per-file body** | One state/style change plus one layout | Lowest toggle latency; preserves code-block identity and render caches; no prune/remount | Hidden subtree still consumes memory and remains in DOM queries; requires stable canonical indexes and a file-body boundary |
| 2 | **Incremental remove/mount at grouped-block granularity** | Proportional to mounted blocks/comments in the target file or current virtual window | Memory-safe; fits the existing virtual-window implementation; preserves unrelated blocks | Async prune/focus behavior; expanding must recreate target widgets; must await and batch mutations |
| 3 | **Update existing hunk-sized grouped-block visuals in place** | Proportional to target file rows | Avoids DOM replacement for code content; reuses `update_block()` | Folding is structural, while `update_rows()` assumes fixed row counts; hunk headers/comments and index remapping still need separate handling |
| 4 | **Replace the outer diff with one line-oriented `ScrollView`** | Height/index-cache update; viewport repaint only | Best asymptotic behavior and smallest code-line DOM | Highest implementation and interaction risk; comments/editors need overlays or a hybrid child strategy |

**Recommended production policy:** combine ranks 1 and 2. Retain and hide file bodies while their total resident row/widget budget is modest; for virtualized or over-budget sections, retain logical/render data but prune only mounted target blocks and lazily remount the window intersection on expansion. Rank 3 remains useful inside that implementation, while rank 4 should be pursued only if profiling shows the hybrid still misses the target.

## Implementation strategy

### 1. Separate canonical data from fold projection

- Stop calling `build_viewed_file_fold_diff()` for an already displayed diff. Keep `_source_diff` as the canonical object and assign canonical line/hunk ordinals once.
- Build a per-file segment table once, for example: path, canonical hunk range, canonical line range, per-mode expanded height, header key, resident body widget, and mounted block set.
- Keep cursor, comments, search, location lookup, widget registries, and DOM IDs keyed by canonical identity. Maintain visible row order separately. Current `(path, line number, side)` maps are suitable external anchors; a canonical source ordinal is safer for duplicate/numberless lines.
- Model a folded file as a header-only segment with zero body height. Do not represent it by deleting source hunks or renumbering later files.
- Initially, rebuilding a lightweight visible-row projection may still be acceptable; the largest win is eliminating content construction and DOM replacement. If projection/geometry then dominates, store per-file heights and prefix sums (or a Fenwick tree) so a fold updates one file height and `y → segment` remains logarithmic.

### 2. Add a file-level render boundary

For non-virtual rendering, mount a stable file header and a non-scrolling file body container. The body owns that file's hunk headers, grouped code blocks, inline comments, and editor layout widgets. Collapsing sets the body to `display: none`; expanding restores it. Do not use `visibility: hidden`.

For virtual rendering, a permanent wrapper for every file may conflict with the current flat top/bottom-buffer ordering. It is reasonable to keep the flat DOM there and use the segment table as the boundary: remove only target blocks/inline annotations currently mounted, recompute the two buffers, and mount only target rows intersecting the new virtual window.

File-level comments attached to the header should remain visible. Inline comments belong to the body. If the existing contract that folded comments are absent from `query()` must remain, remove just comment/editor widgets while hiding retained code blocks; otherwise change the contract to “mounted but `display == False`” and update the folding test deliberately.

### 3. Make the delta one serialized transaction

Both `toggle_current_file_fold()` and viewed-state refresh should call one `_apply_file_fold_delta(path, folded, token)` pathway instead of `show_diff()`.

1. Capture logical cursor/header identity, active side, focus owner, and viewport-relative anchor.
2. Increment/coalesce a fold/render generation so a stale fold, highlight completion, or virtual-window worker cannot commit later.
3. Enter `async with content.batch():`.
4. Update `_folded_file_paths`, visible rows, per-file height/geometry, header content/class, and target comment visibility.
5. Toggle the retained body, or `await` one bulk target removal and one bulk target mount. Reuse virtual buffer/header synchronization helpers.
6. Leave the batch only after mount lifecycle completion.
7. Use `call_after_refresh()` to restore the same logical anchor if still visible, or the target file header if it was folded. Restore focus to `DiffView`/header rather than a hidden or pruned descendant.

Avoid acquiring the same mutation boundary independently in fold and virtual helpers; one coordinator should own ordering. Preserve the existing request-token checks around after-refresh finalization.

### 4. Invalidate narrowly

- Preserve grouped blocks, header widgets, static row caches, base-content cache, search state, and comment maps for unaffected files.
- On expand, request syntax highlighting only for newly visible target rows not already highlighted. Key validity to the canonical source diff, mode, and theme.
- Recompute global width only if the target file supplied the visible maximum. A per-file maximum/multiset avoids scanning all lines. Alternatively, deliberately keep canonical maximum width while folding to prevent horizontal scrollbar jumps, but treat that as an explicit behavior choice.
- Exit or clamp visual selection only when it intersects a newly folded segment; do not reset unrelated cursor UI state.

### 5. Ship a no-flicker stopgap separately

Before the projection refactor, manual `toggle_current_file_fold()` can be brought to parity with `_refresh_viewed_folds()` by keeping the old tree protected until all replacement mounts finish, using `Widget.batch()` rather than repaint batching alone, and restoring the anchor only after refresh. Coalescing rapid viewed-state changes will also help. This is a useful visual fix, but it leaves whole-diff CPU and allocation cost in place and should not be reported as the substantial performance solution.

## Validation plan

Use structural assertions in CI and timing as a developer benchmark; hard wall-clock tests are likely flaky under xdist.

1. Build combined diffs at 100, 1,000, 10,000, and 50,000 lines; toggle a small, medium, and large file near the beginning/middle/end in unified and split modes.
2. Assert a fold delta does **not** call `show_diff()`, `_render_diff()`, or `#diff-content.remove_children()`.
3. Assert headers and blocks belonging to unaffected files preserve object identity.
4. Assert mutation counts are bounded by the target file's mounted block/window intersection, not total diff size.
5. Assert the anchor `(path, side, line/header, viewport offset)` is preserved within one terminal row when folding above/below the viewport; collapsing the anchored file selects its header.
6. Assert rapid mark/unmark/toggle input commits only the newest generation and leaves no orphaned blocks, headers, comments, or pending virtual state.
7. Cover comments, pending drafts, an active editor, active search, visual selection, full-file preview, viewport-driven virtual shifts, and a target that crosses the virtualization threshold.
8. Preserve the existing grouped/virtual tests and add a folding counterpart to the current “small shift avoids full rerender” and “surviving block identity” tests. [Current folding tests](../../tests/test_diff_view_folding.py); [current performance tests](../../tests/test_diff_view_performance.py); [current virtual tests](../../tests/test_diff_virtual.py).
9. Measure event-to-first-post-refresh latency, separately recording projection, DOM transaction, layout, and highlight work. A sound target is at least a 4× median improvement over the current path on a fixed 10k-line fixture, with collapse normally fitting one 60 Hz frame on the reference machine. Treat this as a benchmark target, not a portable CI timeout.

## Risks

- **Index assumptions:** many methods assume `line_index == _all_lines` position. Stable canonical indexes plus a visible projection require a deliberate audit of cursor movement, row lookups, geometry, search, comments, and virtual windows.
- **Zero-height virtual ranges:** simply assigning zero height to thousands of hidden canonical lines can cause a line-index window to contain only hidden rows. Virtual selection must skip folded segments or operate on visible ordinals/segment heights.
- **DOM-query semantics and memory:** `display: none` preserves descendants. The existing comment test expects no matching mounted comment in a folded file; retaining it changes that observable. Hidden editors/workers also need an explicit suspend/remove policy.
- **Focus and drafts:** Textual removes hidden widgets from focus traversal and pruning resets focus. Save draft state and move focus before hiding/removing a body that owns the active editor.
- **Async races:** fold, virtual-window, highlight, resize/mode, and full-file-preview workers can overlap. Locking plus generation checks are required; batching alone is not synchronization.
- **Layout width behavior:** preserving retained content may preserve the widest line and horizontal scrollbar, unlike today's folded projection. Decide whether stability or visible-only width is the product requirement.
- **Highlight validity:** the current new-`FileDiff` identity and shared line objects deserve a regression test; static inspection indicates cache/content validity can diverge after windowed fold cycles, but this was not dynamically reproduced in this research run.

## Sources

### Kept

- [Textual `Widget` API](https://textual.textualize.io/api/widget/) and [v8.2.8 source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widget.py) — normative batch, mount, remove, and awaitable behavior.
- [Textual `App` API](https://textual.textualize.io/api/app/) and [v8.2.8 source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py) — repaint batching and prune/focus/layout behavior.
- [Textual display](https://textual.textualize.io/styles/display/), [visibility](https://textual.textualize.io/styles/visibility/), [v8.2.8 DOM source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/dom.py), and [screen source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/screen.py) — layout and focus consequences of hiding.
- [Textual MessagePump API](https://textual.textualize.io/api/message_pump/) and [v8.2.8 source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/message_pump.py) — after-refresh restoration semantics.
- [Textual Line API guide](https://textual.textualize.io/guide/widgets/#line-api), [ScrollView](https://textual.textualize.io/api/scroll_view/), and [v8.2.8 `OptionList`](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widgets/_option_list.py) — official line-oriented architecture and concrete indexed rendering example.
- Repository files cited inline — direct evidence of the current fold path, state reset, grouped blocks, virtualization, highlighting, and test contracts.

### Dropped

- Textual GitHub issues/PR discussions about flicker and dynamic remounting — useful history but less authoritative than 8.2.8 API/source behavior.
- Textual `master` source snapshots — excluded where a tagged 8.2.8 source was available.
- Third-party diff viewers and generic performance commentary — architectures and runtimes were not directly comparable to this Textual implementation.

## Gaps

No production code or tests were changed. The background researcher had no shell tool, so the parent performed the runtime measurements above; no terminal-frame capture or peak-memory measurement was taken. The speedup and memory profile of a correctness-complete retained-section implementation still need measurement, and the suspected highlight-cache regression was not dynamically reproduced. The first implementation step should add instrumentation around fold request, projection, mutation, layout refresh, and final anchor restoration.
