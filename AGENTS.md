# Repository Guidelines

## Project Structure & Module Organization

TIB-AV-A is a video analysis application organized as a Python `uv` workspace with a Vue 2 frontend.

- `backend/src/backend/`: Django project; application models, views, Celery tasks, migrations, and `backend/tests.py` live in its `backend/` subdirectory.
- `analyser/src/analyser/`: analysis service and client.
- `inference_ray/src/inference_ray/plugins/`: Ray inference plugins.
- `packages/`: shared `data`, `interface` (protobuf/gRPC), and `utils` packages.
- `frontend/src/`: Vue components, views, Pinia stores, and assets; `frontend/public/` holds static files.
- `tests/` and `packages/utils/tests/`: standalone Python tests. `scripts/` contains maintenance utilities; `docs/` documents batch workflows. Runtime media, caches, and models belong under `data/`.

## Build, Test, and Development Commands

Run commands from the repository root unless stated otherwise. Follow `README.md` to prepare data directories and model files first.

- `docker compose up --build`: build and start the development stack.
- `docker compose exec backend uv run --package backend python3 backend/src/backend/manage.py migrate`: apply database migrations.
- In `frontend/`, run `npm install`, then `npm run serve` for hot reload or `npm run build` for a production build.
- `python -m unittest discover -s tests`: run standalone EAF filtering tests.
- `uv run --package utils python -m unittest discover -s packages/utils/tests`: run video decoder tests.
- `docker compose exec backend uv run --package backend python3 backend/src/backend/manage.py test backend.tests`: run Django tests with the stack configured.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions/modules, and `PascalCase` classes. Match nearby frontend code: two-space indentation, camelCase JavaScript identifiers, and PascalCase Vue component filenames. Include Django migrations with model changes. No shared formatter configuration is checked in; the frontend lint script lacks its Vue CLI lint plugin dependency, so use the build for current frontend verification.

## Testing Guidelines

Use Django `TestCase`/`SimpleTestCase` for backend behavior and `unittest` for standalone utilities. Name tests `test_*`; add regression cases for changed behavior and mock external inference calls. No coverage threshold is configured. Run the smallest relevant suite and report unavailable dependencies or services.

## Commit & Pull Request Guidelines

History uses short descriptive subjects without a consistent prefix scheme. Prefer imperative summaries such as `Fix batch export progress`. Keep commits focused. PRs should describe behavior changes, link relevant issues, record verification, and include screenshots for UI changes. Explain migration, configuration, or model requirements.

## Agent Workflow

On Windows, use PowerShell, `rg`, and `apply_patch`. Preserve unrelated changes and keep generated media, caches, and credentials out of commits.
