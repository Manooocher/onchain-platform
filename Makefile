# Makefile targets wrap the tools DOC-010 already commits to — nothing here
# introduces a new decision (DOC-011 § Makefile).

.PHONY: install lint typecheck test test-unit test-integration test-replay import-check run migrate

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src tests

# Fast inner-loop default: the no-service unit category (tests/unit + schema).
test:
	uv run pytest -m unit

# CI job commands (S0-10) — local `make` == CI exactly. Categories are applied
# by marker in tests/conftest.py.
test-unit:
	uv run pytest -m unit

# Integration + replay need Postgres/Redis up (docker compose up -d --wait);
# live network tests are excluded.
test-integration:
	uv run pytest -m "(integration or replay) and not live"

test-replay:
	uv run pytest -m replay

import-check:
	uv run lint-imports

run:
	docker compose up

migrate:
	uv run alembic upgrade head
