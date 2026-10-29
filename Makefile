# Reproducibility entry points (guide B4.6). On Windows without make, use scripts/run_all.ps1.
PY ?= python
CONFIG ?= configs/default.yaml
PORT ?= 8000

.PHONY: install lint test parity experiment serve bench docker-build docker-run all

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	ruff check .

test: lint
	$(PY) -m pytest -rs

parity:
	$(PY) -m tools.parity

# eval/run_experiment.py is owned by Person 3; this target runs it once it is merged.
experiment:
	@if [ -f eval/run_experiment.py ]; then \
		$(PY) -m eval.run_experiment --config $(CONFIG); \
	else \
		echo "eval/run_experiment.py not merged yet (Person 3). Running parity summary instead."; \
		$(PY) -m tools.parity; \
	fi

serve:
	$(PY) -m uvicorn service.api:app --port $(PORT)

bench:
	$(PY) -m tools.bench_latency

docker-build:
	docker build -t upi-stepup .

docker-run:
	docker run --rm -p $(PORT):8000 upi-stepup

all: test experiment serve
