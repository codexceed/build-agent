# Common project commands. Run `make help` for the list.
# These targets are the canonical handles for the project — CLAUDE.md and CI
# should call them rather than invoking the underlying tools directly.

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test check run clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync the virtualenv with project + dev dependencies
	uv sync

lint: ## Run ruff + pylint (pylint enforces Google docstrings)
	uv run ruff check .
	uv run pylint src

format: ## Auto-format and apply mechanical lint fixes
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Run pyright (strict)
	uv run pyright

test: ## Run the test suite
	uv run pytest

check: lint typecheck test ## Run all gates: lint, types, tests

run: ## Run the application entrypoint
	PYTHONPATH=src uv run python -m intake

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
