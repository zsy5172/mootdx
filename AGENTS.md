# Repository Guidelines

## Project Structure & Module Organization
Core library code lives in `mootdx/`. Main entry points include `quotes.py`, `reader.py`, `affair.py`, and `financial/`, with shared helpers under `utils/`, `cache/`, `contrib/`, and `tools/`. Tests live in `tests/`, grouped by feature area such as `tests/reader/`, `tests/quotes/`, and `tests/tools/`; reusable sample data is under `tests/fixtures/`. Documentation sources are in `docs/`, and runnable examples are in `sample/`.

## Build, Test, and Development Commands
Use Poetry for local setup and packaging.

- `poetry install --sync`: install runtime and dev dependencies from `pyproject.toml`.
- `poetry run pytest`: run the full test suite.
- `tox`: run tests across supported Python versions (`py38` through `py312`).
- `make test`: run the default local test target.
- `make cov`: run tests with coverage and generate `htmlcov/`.
- `make fmt`: format code with Black.
- `pre-commit run --all-files`: apply repository hooks before opening a PR.

## Coding Style & Naming Conventions
Follow Python conventions already used in `mootdx/`: 4-space indentation, `snake_case` for functions and modules, `PascalCase` for classes, and concise module-level APIs. Keep public modules focused on one data domain. Pre-commit hooks enforce LF endings, trimmed whitespace, import reordering, and single-quote normalization. Black is configured with a 120-character target; `make lint` also runs Flake8, so keep new code readable even when longer lines are technically allowed.

## Testing Guidelines
Pytest is the primary test framework, with `pytest-cov`, `pytest-datadir`, and `freezegun` used in the suite. Add tests next to the affected domain, following the existing `test_*.py` pattern, for example `tests/quotes/test_quotes_std.py`. Prefer fixture-backed tests when parsing local TDX data, and cover both happy paths and empty-data or reconnect edge cases when touching network or parsing logic.

## Commit & Pull Request Guidelines
Recent history uses short, typed subjects such as `feat: ...`, `fix: ...`, and `doc: ...`; keep that format for new commits. Commitizen is configured and checked by pre-commit, so write messages that can pass `cz check`. PRs should include a clear summary, note any API or behavior changes, link related issues, and mention the exact verification run (for example `poetry run pytest` or `tox`). Include screenshots only when documentation or generated site output changes.
