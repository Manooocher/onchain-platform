# Makefile targets wrap the tools DOC-010 already commits to — nothing here
# introduces a new decision (DOC-011 § Makefile).

.PHONY: install lint typecheck test test-replay import-check run migrate

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src/

# Deliberately excludes tests/replay/ — bundling it in would make the everyday
# inner-loop command slow enough that people stop running it (DOC-011).
test:
	uv run pytest tests/unit tests/integration tests/schema

test-replay:
	uv run pytest tests/replay

import-check:
	uv run lint-imports

run:
	docker compose up

migrate:
	uv run alembic upgrade head
