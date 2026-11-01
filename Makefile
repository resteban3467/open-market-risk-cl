.PHONY: check lint format typecheck test clean

check: lint format typecheck test

lint:
	uv run ruff check --fix .

format:
	uv run ruff format --check .

typecheck:
	uv run ty check

test:
	uv run pytest -q

clean:
	rm -rf .ruff_cache .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
