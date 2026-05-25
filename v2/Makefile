# ITIPS V2.1 — developer ergonomics.
# Run `make help` for the menu.

.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

DOCKER ?= docker
COMPOSE ?= docker compose
PYTHON ?= python3

.PHONY: help install build up down logs restart shell ps test lint fmt typecheck check clean fresh

help:  ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	      /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install Python deps locally (dev only, not needed for docker)
	$(PYTHON) -m pip install -e ".[dev]"

build:  ## Build the docker image
	$(COMPOSE) build

up:  ## Start the container (works on Mac and Jetson identically)
	@mkdir -p ./var ./evidence_store
	$(COMPOSE) up -d
	@echo ""
	@echo "  Dashboard:  http://localhost:5050/dashboard"
	@echo "  API docs:   http://localhost:5050/docs"
	@echo "  Inbound:    http://localhost:8443/docs (bearer required)"
	@echo ""

down:  ## Stop the container
	$(COMPOSE) down

logs:  ## Follow container logs
	$(COMPOSE) logs -f --tail=200 itips

restart: down up  ## Restart

ps:  ## Show container status
	$(COMPOSE) ps

shell:  ## Open a shell inside the running container
	$(COMPOSE) exec itips bash

test:  ## Run pytest
	$(PYTHON) -m pytest

lint:  ## Lint with ruff
	$(PYTHON) -m ruff check .

fmt:  ## Format with ruff
	$(PYTHON) -m ruff format .

typecheck:  ## mypy
	$(PYTHON) -m mypy itips

check: lint typecheck test  ## Run all quality gates

clean:  ## Remove pycache + test artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info .venv-refactor

fresh: clean down  ## Hard reset — clean + down (does NOT touch evidence_store/ or var/)
	@echo "Cleaned. Evidence and intake DB preserved on host."
