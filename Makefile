.PHONY: test lint format typecheck simulate diagrams eval eval-live

PYTHON ?= .venv/Scripts/python.exe
PYTEST ?= .venv/Scripts/pytest.exe
RUFF ?= .venv/Scripts/ruff.exe
MYPY ?= .venv/Scripts/mypy.exe

test:
	$(PYTEST) --cov=src/incident_agent --cov-report=term-missing

lint:
	$(RUFF) check src tests scripts
	$(RUFF) format --check src tests scripts

format:
	$(RUFF) format src tests scripts

typecheck:
	$(MYPY) --strict src

simulate:
	$(PYTHON) scripts/simulate_incident.py --write-example-postmortem docs/postmortem-example.md

diagrams:
	$(PYTHON) scripts/render_diagrams.py

eval:
	$(PYTHON) -m tests.eval.test_eval_suite

eval-live:
	$(PYTHON) -m tests.eval.test_eval_suite --live
