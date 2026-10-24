# Contributing to TurnPing

Thanks for helping! TurnPing aims to be small, reliable and honest about what it supports.

## Setup

```powershell
py -3 -m venv C:\venvs\turnping          # keep venvs out of synced folders (OneDrive)
C:\venvs\turnping\Scripts\pip install -e ".[dev]"
```

## Before opening a pull request

```powershell
ruff check src tests scripts packaging docs/examples
ruff format --check src tests scripts packaging docs/examples
mypy
python -m pytest -q          # desktop tests: set QT_QPA_PLATFORM=offscreen on headless machines
```

All must pass; CI runs the same on Windows with Python 3.12–3.14.

## Guidelines

- **Tests first-class.** Every bug fix gets a regression test. Don't weaken or skip tests
  to get green.
- **Never touch the developer's real environment in tests.** The `isolated_home`
  fixture redirects `TURNPING_HOME` and `TURNPING_USER_HOME`; registry writes and OS
  toasts must be mocked.
- **Semantics over convenience.** Map provider events only to categories the source
  documents. "Response finished" is not "task completed".
- **Privacy.** Never copy prompts, tool input/output, transcripts or environment
  variables into events, logs or fixtures.
- **No new network listeners, telemetry, LLM calls or shell execution.**
- **Integrations** follow [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md#adding-a-provider).
  State clearly what you verified against a real install versus fixtures.
- Keep modules focused; UI code never touches SQLite or files on the GUI thread.

## Releases

Maintainers bump `version` in `pyproject.toml` and `src/turnping/__init__.py`, update
`CHANGELOG.md` and `packaging/version_info.txt`, and push a `vX.Y.Z` tag. The release
workflow builds the zip and creates a **draft** release for manual review.

By contributing you agree your work is licensed under the MIT License and that you follow
the [Code of Conduct](CODE_OF_CONDUCT.md).
