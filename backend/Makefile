SERVICE_NAME = codex-proxy-backend
IMAGE_NAME = codex-proxy/backend:latest
COMPOSE = docker compose -f ../docker-compose.yaml

.PHONY: lint format test down build up logs shell deploy

# ----------Local checks----------
# Lint and format checks.
lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

# ----------Production commands (Docker)----------
down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d

logs:
	$(COMPOSE) logs -f

shell:
	$(COMPOSE) exec $(SERVICE_NAME) /bin/bash

deploy: build down up
	@echo "Deployment complete"

# Catch-all target to prevent Make from treating script arguments as targets
%:
	@:
