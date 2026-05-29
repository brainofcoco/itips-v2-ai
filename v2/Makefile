# ITIPS V2.1 — developer ergonomics.
# Run `make help` for the menu.

.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

DOCKER ?= docker
COMPOSE ?= docker compose
PYTHON ?= python3

COMPOSE_JETSON ?= $(COMPOSE) -f docker-compose.yml -f docker-compose.jetson.yml

.PHONY: help install build build-ml up down logs restart shell ps test lint fmt typecheck check clean fresh \
        build-jetson up-jetson logs-jetson \
        dash-up dash-build dash-down dash-logs dash-dev

help:  ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	      /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install Python deps locally (dev only, not needed for docker)
	$(PYTHON) -m pip install -e ".[dev]"

build:  ## Build the lightweight core image (no ML, no GPU)
	$(COMPOSE) build

build-ml:  ## Build the CPU ML image for Mac/x86 validation
	$(DOCKER) build --target ml-cpu -t itips/edge:2.1.0-ml .

up:  ## Start the core container (Mac/lab)
	@mkdir -p ./var ./evidence_store
	$(COMPOSE) up -d
	@echo ""
	@echo "  Dashboard:  http://localhost:5050/dashboard"
	@echo "  API docs:   http://localhost:5050/docs"
	@echo "  Inbound:    http://localhost:8443/docs (bearer required)"
	@echo ""

build-jetson:  ## Build the Jetson GPU image (run on the Jetson)
	$(COMPOSE_JETSON) build

up-jetson:  ## Start the Jetson GPU stack (run on the Jetson)
	@mkdir -p ./var ./evidence_store
	$(COMPOSE_JETSON) up -d
	@echo ""
	@echo "  Dashboard:  http://localhost:5050/dashboard"
	@echo ""

logs-jetson:  ## Follow Jetson container logs
	$(COMPOSE_JETSON) logs -f --tail=200 itips

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

# ─── Vite/React dashboard (opt-in `dev` profile) ────────────────────
dash-up:  ## Start itips + dashboard (Mac/lab — bundles a frontend)
	@mkdir -p ./var ./evidence_store
	@# Try a rebuild, but if BuildKit can't reach Docker Hub to resolve
	@# base-image metadata (a common transient outage), fall back to
	@# starting with the images already built locally so the stack still
	@# comes up. Use `make dash-build` to force a rebuild and surface the
	@# error instead.
	@COMPOSE_PROFILES=dev $(COMPOSE) up -d --build || { \
		echo ""; \
		echo "  ⚠ build failed (Docker Hub unreachable?) — starting with existing local images"; \
		COMPOSE_PROFILES=dev $(COMPOSE) up -d; \
	}
	@echo ""
	@echo "  Vite dashboard: http://localhost:3000"
	@echo "  Legacy console: http://localhost:5050/dashboard"
	@echo ""

dash-build:  ## Force a rebuild of itips + dashboard (fails loudly if Hub is down)
	@mkdir -p ./var ./evidence_store
	COMPOSE_PROFILES=dev $(COMPOSE) up -d --build

dash-down:  ## Stop both services (itips + dashboard)
	COMPOSE_PROFILES=dev $(COMPOSE) down

dash-logs:  ## Tail dashboard container logs
	$(COMPOSE) logs -f --tail=200 dashboard

dash-dev:  ## Run the Vite dev server on the host (HMR) against running itips
	cd dashboard && npm install && npm run dev
