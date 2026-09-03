# Wrap the commands contributors would otherwise have to remember.
.DEFAULT_GOAL := help
SHELL := /bin/bash
# Prefer the venv `make setup` builds; fall back to the system interpreter so a
# fresh checkout can still run the safeguard checks before installing anything.
BOOTSTRAP_PY ?= python3.12
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo $(BOOTSTRAP_PY))
COMPOSE := docker compose
DEV_COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help setup setup-frontend build-frontend serve up dev test lint denylist check-pii check-pii-history fixtures fixtures-check clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup:  ## Create the venv, install dev deps, install git hooks
	$(BOOTSTRAP_PY) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"
	.venv/bin/pre-commit install
	@mkdir -p data/incoming data/db
	@echo
	@echo "Ready. data/incoming and data/db are gitignored — put real reports there."
	@echo "Run 'make check-pii' before every commit; the hook runs it too."

setup-frontend:  ## Install the UI's build dependencies
	cd frontend && npm install

build-frontend:  ## Build the UI into the Python package
	cd frontend && npm run build

serve:  ## Run the app locally without Docker (needs make build-frontend first)
	$(PY) -m unbagged.cli serve

up:  ## Start the app (single container)
	@test -f docker-compose.yml || { echo "Docker packaging lands in M6 (see HANDOFF.md §9)."; exit 1; }
	$(COMPOSE) up

dev:  ## Start the app with a Vite dev server and HMR
	@test -f docker-compose.dev.yml || { echo "Dev overlay lands in M6 (see HANDOFF.md §9)."; exit 1; }
	$(DEV_COMPOSE) up

test:  ## Run the test suite
	$(PY) -m pytest -q

lint:  ## Lint with ruff
	$(PY) -m ruff check .

denylist:  ## Build tools/denylist.txt from your own report (REPORT=path)
	@test -n "$(REPORT)" || { echo "Usage: make denylist REPORT=data/incoming/your-report.pdf"; exit 1; }
	$(PY) tools/build_denylist.py "$(REPORT)"

check-pii:  ## Scan the working tree for personal data
	$(PY) tools/scan_pii.py

check-pii-history:  ## Scan commit messages and diffs across all history
	$(PY) tools/scan_pii.py --history

fixtures:  ## Regenerate synthetic fixtures
	$(PY) tools/make_fixtures.py

fixtures-check:  ## Fail if a committed fixture is not generator output
	$(PY) tools/make_fixtures.py --check

clean:  ## Remove build and cache artifacts (never touches data/)
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
