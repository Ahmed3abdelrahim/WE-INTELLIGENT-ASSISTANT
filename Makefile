SHELL := /bin/bash
COMPOSE := docker compose
PY := python3

.PHONY: models smoke crawl ingest up up-gpu up-cloud down test eval eval-compare notebook \
        native-up native-down native-smoke

# --- Spec-defined targets (container stack; see docs/progress.md for the native fallback used
#     on this laptop, which has no Docker daemon — docs/decisions.md) ---

models:
	$(PY) scripts/download_models.py

crawl:
	$(COMPOSE) --profile setup run --rm setup python -m app.ingestion.crawl_te || \
	$(PY) scripts/crawl_te.py

ingest:
	$(COMPOSE) --profile setup run --rm setup python scripts/ingest_website.py || \
	$(PY) scripts/ingest_website.py

up:
	$(COMPOSE) -f compose.yaml up -d --build

up-gpu:
	$(COMPOSE) -f compose.yaml -f compose.gpu.yaml up -d --build

up-cloud:
	$(COMPOSE) -f compose.yaml -f compose.cloud.yaml up -d --build

down:
	$(COMPOSE) down

smoke:
	$(PY) scripts/smoke_all.py

test:
	$(PY) -m pytest tests -v

eval:
	$(PY) eval/run_eval.py

eval-compare:
	$(PY) eval/run_eval.py --compare

notebook:
	$(COMPOSE) --profile notebook up -d --build

# --- Native fallback (no Docker daemon on this machine): runs the same backend/service code
#     as host processes instead of containers. See docs/decisions.md. ---

native-up:
	bash scripts/native_up.sh

native-down:
	bash scripts/native_down.sh

native-smoke: native-up smoke
