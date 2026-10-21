# Wrap the commands contributors would otherwise have to remember.
.DEFAULT_GOAL := help
SHELL := /bin/bash
# Prefer the venv `make setup` builds; fall back to the system interpreter so a
# fresh checkout can still run the safeguard checks before installing anything.
BOOTSTRAP_PY ?= python3.12
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo $(BOOTSTRAP_PY))
COMPOSE := docker compose
DEV_COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help fmt format hooks check pre-push types typecheck-frontend size size-report size-update setup setup-frontend build-frontend serve up down logs reset dev test test-frontend test-container setup-browser lint denylist check-pii check-pii-history fixtures fixtures-check lock lock-check brand brand-check icons-check screenshots clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup:  ## Create the venv, install dev deps, install git hooks
	$(BOOTSTRAP_PY) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"
	$(MAKE) hooks
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

# --build is load-bearing. Compose reuses an image already tagged
# unbagged:local rather than noticing the checkout moved, so without it `make
# up` serves whatever was built last: after a pull, yesterday's code under
# today's source tree. The footer is the only tell, and it is not lying when it
# says the old number — the container really is that version. Measured cost of
# the rebuild when nothing changed: ~2s.
up:  ## Start the app, then open http://localhost:8420
	$(COMPOSE) up --build

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
	$(DEV_COMPOSE) up --build

test:  ## Run the fast test suite
	$(PY) -m pytest -q

test-frontend:  ## Run the UI unit tests (vitest)
	cd frontend && npm test

test-container:  ## Run the slow tests that build and run a real container
	$(PY) -m pytest -q -m container

setup-browser:  ## Install Chromium for the browser tests
	$(PY) -m pip install -e ".[browser]"
	$(PY) -m playwright install chromium -p no:cacheprovider

fmt:  ## Apply the formatters (see CODING_STANDARDS.md)
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix --select I .
	cd frontend && npm run -s format

format: fmt  ## Alias for fmt

hooks:  ## Install the pre-commit, commit-msg and pre-push hooks
	$(PY) -m pre_commit install

size:  ## Fail on a file past the size limit, or a baselined file that grew
	$(PY) -m tools.check_size

size-report:  ## List every file over the preferred size
	$(PY) -m tools.check_size --report

size-update:  ## Lower the size baseline to today's counts (it never rises)
	$(PY) -m tools.check_size --update

types:  ## Python type check with ty: advisory, reports and never fails
	-$(PY) -m ty check

typecheck-frontend:  ## Type-check the UI (tsc, no emit)
	cd frontend && npm run typecheck

# What the pre-push hook runs: the checks too slow for every commit. Fails
# with a pointer rather than a stack trace when the UI was never installed.
pre-push:  ## The pre-push gate: UI type-check, then both unit-test tiers
	@test -d frontend/node_modules || { echo "frontend/node_modules is missing: run 'make setup-frontend' once."; exit 1; }
	$(MAKE) --no-print-directory typecheck-frontend
	$(MAKE) --no-print-directory test
	$(MAKE) --no-print-directory test-frontend

check:  ## Every repo gate that needs no Docker: PII, fixtures, lock, then every hook
	$(PY) -m tools.scan_pii
	$(PY) -m tools.make_fixtures --check
	$(PY) -m tools.check_lock
	$(PY) -m tools.check_size
	$(PY) -m pre_commit run --all-files
	$(PY) -m pre_commit run --all-files --hook-stage manual editorconfig-checker

lint:  ## Lint and check formatting: ruff; Prettier and ESLint over the UI
	$(PY) -m ruff check .
# The half that had never run. `ruff check` and `ruff format` police
# different things, and CLAUDE.md has always claimed both.
	$(PY) -m ruff format --check .
	cd frontend && npm run -s format:check
	cd frontend && npm run -s lint

denylist:  ## Build tools/denylist.txt from your own report (REPORT=path)
	@test -n "$(REPORT)" || { echo "Usage: make denylist REPORT=data/incoming/your-report.pdf"; exit 1; }
	$(PY) -m tools.build_denylist "$(REPORT)"

check-pii:  ## Scan the working tree for personal data
	$(PY) -m tools.scan_pii

check-pii-history:  ## Scan commit messages and diffs across all history
	$(PY) -m tools.scan_pii --history

fixtures:  ## Regenerate synthetic fixtures
	$(PY) -m tools.make_fixtures

fixtures-check:  ## Fail if a committed fixture is not generator output
	$(PY) -m tools.make_fixtures --check

lock:  ## Regenerate docker/requirements.txt (needs Docker; runs on linux/amd64)
	$(PY) -m tools.make_lock

lock-check:  ## Fail if the lock no longer covers pyproject's dependencies
	$(PY) -m tools.check_lock

brand:  ## Regenerate frontend/public/ from resources/ (strips C2PA manifests)
	$(PY) -m tools.build_brand

brand-check:  ## Fail if a served brand asset is not what its source produces
	$(PY) -m tools.build_brand --check

# The gate CI runs on every pull request, available locally against whatever you
# would open the PR against. Override for a stacked branch: make icons-check BASE=origin/other
BASE ?= origin/main

icons-check:  ## Fail if a source SVG changed without the icons it produces
	$(PY) -m tools.check_icon_sync $(BASE)

screenshots:  ## Regenerate docs/screenshots from the synthetic fixture (needs Docker + browser)
	$(PY) -m tools.make_screenshots

clean:  ## Remove build and cache artifacts (never touches data/)
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
