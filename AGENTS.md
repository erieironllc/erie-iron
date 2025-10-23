# Repository Guidelines

## Project Structure & Module Organization
- `erieiron_common/` supplies shared enums, AWS helpers, and LLM adapters.
- `erieiron_autonomous_agent/` owns task automation, agent flows, and management commands.
- `erieiron_ui/` delivers the Django UI plus Backbone assets in `js/` and Sass styles in `sass/`.
- Tests live in `tests/` and module-specific `*/tests`; add coverage beside the code you touch.
- Operational configs (`settings.py`, `pyproject.toml`, `package.json`) and deployment scripts (`scripts/`, `aws/`) round out the stack.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` sets up an isolated runtime (use `.venv\Scripts\activate` on Windows).
- `pip install -r requirements.txt && pip install -e .` brings in Python dependencies and installs the local package for editable development.
- `python manage.py migrate && python manage.py runserver` applies migrations and serves the Django UI on `localhost:8000`.
- `pytest` runs the Python suite; add paths (e.g., `pytest tests/ui`) to focus on a module.
- `npm install` once, then `npm run compile-ui` builds the Backbone/Bootstrap bundle; `npm run watch` rebuilds assets on file change.

## Coding Style & Naming Conventions
- Python code is formatted with Black (configured in `pyproject.toml`) and linted via Ruff; keep 4-space indentation, `snake_case` functions, `CamelCase` classes, and uppercase Django settings/constants.
- JavaScript follows ESLint defaults and Backbone patterns—prefer `const`/`let`, keep views in `erieiron_ui/js/`, and mirror existing Sass naming in `erieiron_ui/sass/`.
- JSON/YAML configs stay ASCII and sorted where practical; mirror established prompt and agent naming.

## Testing Guidelines
- Pytest with `pytest-django` is the primary framework; name files `test_*.py` and group fixtures in `conftest.py` near their domain.
- Include regression coverage for every bug fix; high-value paths (LLM orchestration, iteration workflows) should assert both happy and failure cases.
- Use `pytest --maxfail=1` locally before pushing to surface flaky failures early; aim to keep suites deterministic and side-effect free.

## Commit & Pull Request Guidelines
- Follow the existing history: concise, sentence-style subject lines that describe the change (e.g., “Refactor task status badges...”).
- Commits should be logically scoped; prefer splitting sweeping updates into reviewable chunks.
- PRs must describe intent, linked tickets, and validation steps (`pytest`, `npm run compile-ui`, screenshots for UI changes). Note config or migration impacts explicitly.
- Request domain reviewers (agent, UI, or infra) aligned with the touched modules; include follow-up tasks when deferring non-blocking work.
