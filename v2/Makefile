# ITIPS V2 — developer ergonomics
# Run `make help` for the menu.

.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

DOCKER ?= docker
COMPOSE ?= docker compose
PYTHON ?= python3

.PHONY: help install build up down logs restart sim sim-down sim-logs test lint fmt typecheck check clean fresh
.PHONY: shell bench export-trt

help:  ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	      /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install Python deps locally (dev)
	$(PYTHON) -m pip install -e ".[dev]"

# A working `nvidia-smi` is the cheapest "am I on a Jetson?" check. If
# we're on a laptop the production targets refuse to run and point the
# user at `make sim` instead — much friendlier than a 60-second
# DeadlineExceeded on the L4T base image.
define require_jetson
	@command -v nvidia-smi >/dev/null 2>&1 || { \
		echo ""; \
		echo "  ✗ This target is for the Jetson only (nvidia-smi not found)."; \
		echo "  → On a laptop, run 'make sim' instead."; \
		echo ""; \
		exit 1; \
	}
endef

build:  ## Build the Jetson docker image (Jetson only — use 'make sim' on a laptop)
	$(call require_jetson)
	$(COMPOSE) build

up:  ## Start the Jetson container (Jetson only — use 'make sim' on a laptop)
	$(call require_jetson)
	$(COMPOSE) up -d

down:  ## Stop the Jetson container
	$(COMPOSE) down

logs:  ## Follow container logs
	$(COMPOSE) logs -f --tail=200 itips

restart: down up  ## Restart the Jetson container

sim:  ## Start simulation mode (MediaMTX + ffmpeg + ITIPS, no GPU required)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.sim.yml up -d --build

sim-down:  ## Stop simulation
	$(COMPOSE) -f docker-compose.yml -f docker-compose.sim.yml down

sim-logs:  ## Follow simulation logs
	$(COMPOSE) -f docker-compose.yml -f docker-compose.sim.yml logs -f --tail=200

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

export-trt:  ## Convert yolo11n.pt → yolo11n.engine on the Jetson
	./scripts/export_yolo_tensorrt.sh

bench:  ## Benchmark inference FPS on the Jetson
	$(PYTHON) -m itips.tools.bench

clean:  ## Remove pycache + test artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info

fresh: clean down  ## Hard reset — clean + down (does NOT touch evidence_store)
	@echo "Cleaned. Evidence and intake DB preserved on the NVMe."
