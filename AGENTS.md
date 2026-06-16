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
- You can run test server: textual serve "rit https://github.com/mizisu/rit/pull/1"
- `textual serve` defaults to a 16px web terminal font; for a local-terminal-like scale, open the served URL with `?fontsize=12` (roughly browser zoom 75%).

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

- Standard test command (project tests only):
  - `uv run pytest -q tests`
- This full-suite command is configured to parallelize with `pytest-xdist` from `pyproject.toml`; keep it reasonably fast and investigate meaningful runtime regressions.
- Do **not** rely on bare `uv run pytest` in this workspace; it can pick up unrelated local folders.
- Do not add marker-selected test groups, fast-suite scripts, or alternate default test commands. Keep the normal path as the full suite.
- Keep Textual UI tests high-value and non-duplicative:
  - Prefer lower-level store/widget tests when they cover the same behavior as a full app-driver smoke test.
  - Avoid adding multiple `App.run_test()` variants for small edge cases unless the integration behavior itself is the risk.
  - Replace fixed sleep stacks with event/state based waits, such as the shared `wait_until` helper in `tests/conftest.py`.
- If a test flakes under xdist, fix the synchronization or isolation issue instead of disabling parallelism or carving out a separate group.
- Before finishing, report the exact full-suite result and elapsed time from `uv run pytest -q tests` when you run it.

## Type checking

- Run: `uv run ty check src`
- Do not introduce new type errors.

## PR instructions

- Keep changes scoped and minimal.
- Add/update tests for behavior changes.
- Before finishing, report exactly which checks were run and their results.
- Do not commit generated artifacts or caches.
