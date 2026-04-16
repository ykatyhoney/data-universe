SHELL := /usr/bin/env bash
OPS_DIR := ops

# venv layout differs between POSIX (.venv/bin) and Windows (.venv/Scripts).
ifeq ($(OS),Windows_NT)
  VENV_BIN := ops/.venv/Scripts
  EXE := .exe
else
  VENV_BIN := ops/.venv/bin
  EXE :=
endif

VENV := $(OPS_DIR)/.venv
PY   := $(VENV_BIN)/python$(EXE)
PIP  := $(PY) -m pip

.PHONY: help bootstrap preflight init-db migrate \
        install-web build-web dev-web \
        start stop restart reload status logs \
        test lint fmt typecheck clean nuke

help:
	@echo "Native ops stack — requires Redis + PostgreSQL running locally and pm2 on PATH."
	@echo ""
	@echo "Setup:"
	@echo "  make bootstrap     — create ops/.venv, install ops[dev]"
	@echo "  make preflight     — verify redis + postgres are reachable"
	@echo "  make init-db       — create ops/staging schemas (M0 sentinel)"
	@echo "  make migrate       — alembic upgrade head"
	@echo "  make install-web   — npm ci in ops/dashboard/web/"
	@echo "  make build-web     — npm run build → ops/dashboard/web/dist/"
	@echo "  make dev-web       — npm run dev (Vite hot reload on :5173, proxies to :8000)"
	@echo ""
	@echo "Run (via pm2):"
	@echo "  make start         — pm2 start ecosystem.config.js"
	@echo "  make stop          — pm2 stop all"
	@echo "  make restart       — pm2 restart all"
	@echo "  make reload        — pm2 reload ecosystem.config.js (zero-downtime for enabled apps)"
	@echo "  make status        — pm2 ls"
	@echo "  make logs          — pm2 logs (tail)"
	@echo ""
	@echo "Dev:"
	@echo "  make test          — pytest"
	@echo "  make lint          — ruff check"
	@echo "  make fmt           — ruff format + --fix"
	@echo "  make typecheck     — mypy"
	@echo "  make clean         — rm venv + caches"
	@echo "  make nuke          — clean + pm2 delete all"

# ---------- Python env ---------- #

$(VENV):
	python -m venv $(VENV)

bootstrap: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(OPS_DIR)[dev]"

# ---------- Preflight + DB ---------- #

preflight: bootstrap
	cd $(OPS_DIR) && ../$(PY) -m scripts.check_services

init-db: bootstrap
	cd $(OPS_DIR) && ../$(PY) -m scripts.init_db

migrate: bootstrap
	cd $(OPS_DIR) && ../$(PY) -m alembic upgrade head

# ---------- Frontend ---------- #

WEB_DIR := $(OPS_DIR)/dashboard/web

install-web:
	cd $(WEB_DIR) && npm install

build-web: install-web
	cd $(WEB_DIR) && npm run build

dev-web:
	cd $(WEB_DIR) && npm run dev

# ---------- pm2 lifecycle ---------- #

start:
	pm2 start ecosystem.config.js

stop:
	pm2 stop ecosystem.config.js

restart:
	pm2 restart ecosystem.config.js

reload:
	pm2 reload ecosystem.config.js

status:
	pm2 ls

logs:
	pm2 logs --lines 200

# ---------- Dev ---------- #

test: bootstrap
	cd $(OPS_DIR) && ../$(PY) -m pytest

lint: bootstrap
	$(PY) -m ruff check $(OPS_DIR)

fmt: bootstrap
	$(PY) -m ruff format $(OPS_DIR)
	$(PY) -m ruff check --fix $(OPS_DIR)

typecheck: bootstrap
	cd $(OPS_DIR) && ../$(PY) -m mypy shared dashboard datastore normalizer pipeline self_validator scripts

clean:
	rm -rf $(VENV)
	find $(OPS_DIR) -type d -name __pycache__ -prune -exec rm -rf {} +
	find $(OPS_DIR) -type d -name ".mypy_cache" -prune -exec rm -rf {} +
	find $(OPS_DIR) -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find $(OPS_DIR) -type d -name ".ruff_cache" -prune -exec rm -rf {} +

nuke: clean
	-pm2 delete ecosystem.config.js 2>/dev/null || true
