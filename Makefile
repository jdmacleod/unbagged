# Wrap the commands contributors would otherwise have to remember.
.DEFAULT_GOAL := help
SHELL := /bin/bash
# Prefer the venv `make setup` builds; fall back to the system interpreter so a
# fresh checkout can still run the safeguard checks before installing anything.
BOOTSTRAP_PY ?= python3.12
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo $(BOOTSTRAP_PY))
COMPOSE := docker compose
DEV_COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help setup setup-frontend build-frontend serve up down logs reset dev test test-frontend test-container setup-browser lint denylist check-pii check-pii-history fixtures fixtures-check lock lock-check clean

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

up:  ## Start the app, then open http://localhost:8420
	$(COMPOSE) up

down:  ## Stop the app and remove its container
	$(COMPOSE) down

logs:  ## Follow the app's logs
	$(COMPOSE) logs -f

reset:  ## Move ./data aside and start empty (CONFIRM=yes required)
	@test "$(CONFIRM)" = "yes" || { \
		echo "This moves ./data aside — your uploaded reports and your database."; \
		echo "Nothing is deleted: it is renamed to data.bak-<timestamp>, so you can"; \
		echo "move it back or remove it yourself once you are sure."; \
		echo ""; \
		echo "  make reset CONFIRM=yes"; \
		exit 1; }
	$(COMPOSE) down 2>/dev/null || true
	@if [ -d data ]; then \
		backup="data.bak-$$(date +%Y%m%d-%H%M%S)"; \
		mv data "$$backup"; \
		echo "Moved ./data to ./$$backup — delete it yourself when you are ready:"; \
		echo "  rm -rf $$backup"; \
	else \
		echo "No ./data directory to move."; \
	fi

dev:  ## Start the dev stack, then open http://localhost:5173
	@echo "Dev mode serves one URL: http://localhost:5173 (API proxied at /api)."
	$(DEV_COMPOSE) up

test:  ## Run the fast test suite
	$(PY) -m pytest -q

test-frontend:  ## Run the UI unit tests (vitest)
	cd frontend && npm test

test-container:  ## Run the slow tests that build and run a real container
	$(PY) -m pytest -q -m container

setup-browser:  ## Install Chromium for the layout regression test
	$(PY) -m pip install -e ".[browser]"
	$(PY) -m playwright install chromium -p no:cacheprovider

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

lock:  ## Regenerate docker/requirements.txt (needs Docker; runs on linux/amd64)
	$(PY) tools/make_lock.py

lock-check:  ## Fail if the lock no longer covers pyproject's dependencies
	$(PY) tools/check_lock.py

clean:  ## Remove build and cache artifacts (never touches data/)
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
