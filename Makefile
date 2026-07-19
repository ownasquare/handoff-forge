.PHONY: sync format lint type test security build demo ui validate

sync:
	uv sync

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .

type:
	uv run mypy src

test:
	uv run pytest --disable-socket -m "not live and not e2e" --cov=handoff_forge --cov-branch

security:
	uv run bandit -q -r src
	uv run pip-audit

build:
	uv build

demo:
	uv run python scripts/generate_demo_assets.py
	uv run handoff-forge demo

ui:
	uv run handoff-forge ui --port 8517

validate: lint type test security build
	uv run python scripts/check_public_repo.py
	@echo "Local code and package validation passed. Browser E2E, live providers, containers, and hosted CI are separate proof layers."
