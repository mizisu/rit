# AGENTS.md

## Project overview

- `rit` is a Textual TUI for reviewing GitHub Pull Requests in the terminal.
- Language/runtime: Python 3.14+
- Package/dependency manager: `uv`
- GitHub integration: `gh` CLI (GraphQL + REST via subprocess)
- Main entrypoints:
  - `uv run rit <pr_ref>`
  - `uv run python -m rit <pr_ref>`
- Supported `pr_ref` formats:
  - `123`
  - `owner/repo#123`
  - `https://github.com/owner/repo/pull/123`

## Dev environment tips

- Install dependencies: `uv sync --dev`
- Verify GH auth before runtime work: `gh auth status`
- Work from repo root (`/Users/charles/Desktop/src/rit`).
- Focus on tracked code in:
  - `src/rit/`
  - `tests/`
- You can run test server: textual serve "rit {pull request url}"

## Architecture quick map

- `src/rit/cli.py` — CLI parsing and app bootstrap
- `src/rit/app.py` — Textual `App`, global bindings, theme/settings wiring
- `src/rit/ui/screens/main.py` — main tabbed screen (`PR Info`, `Files`)
- `src/rit/ui/components/` — tab-level composites (`PRInfo`, `PRTimeline`, `FileChanges`)
- `src/rit/ui/widgets/` — reusable widgets (`DiffView`, `FileTree`, `Header`, etc.)
- `src/rit/state/store.py` — central PR state + loading orchestration
- `src/rit/services/github.py` — all `gh` CLI interactions
- `src/rit/core/diff.py` + `src/rit/core/types.py` — diff parsing + data structures

## Code style and implementation rules

- Keep strong typing throughout (Python 3.14 type syntax is used).
- Keep concise docstrings for public classes/functions.
- No comments that restate what code does. Use descriptive names and small functions instead. If a comment is needed, the code isn't clear enough. Only comment on *why*, never on *what*.
- Follow existing Textual patterns:
  - `reactive` / `var` state + `watch_*`
  - dataclass-based `Message` events
  - `@on(...)` event handlers
  - `run_worker(...)` for async UI work
  - `asyncio.to_thread(...)` for CPU-bound tasks
- Route GitHub operations through `GitHubService` (not directly from UI widgets).
- Preserve stable widget IDs/classes used by tests (e.g. `#file-tree`, `#diff-view-main`, `#line-*`).
- `DiffView.show_diff(...)` is `async`; always `await` it or run via worker.

## Testing instructions

- Preferred full run (project tests only):
  - `uv run pytest -q tests`
- Do **not** rely on bare `uv run pytest` in this workspace; it can pick up unrelated local folders.
- Focused runs:
  - Diff core: `uv run pytest -q tests/test_diff.py`
  - Diff visual/navigation: `uv run pytest -q tests/test_diff_view_visual_mode.py`
  - Models/timeline parsing: `uv run pytest -q tests/test_models.py tests/test_collapsible_markdown.py`
- Current baseline note (important):
  - `tests/test_diff_view_duplicate_id.py` currently fails on this branch (tests are out of sync with async `show_diff` / removed `_content_id`).
  - Treat additional failures beyond that as regressions.

## Type checking

- Run: `uv run ty check src`
- Current baseline has one known type diagnostic in `src/rit/ui/widgets/diff_visual.py`.
- Do not introduce new type errors.

## PR instructions

- Keep changes scoped and minimal.
- Add/update tests for behavior changes.
- Before finishing, report exactly which checks were run and their results.
- Do not commit generated artifacts or caches.
