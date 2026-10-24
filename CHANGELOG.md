# Changelog

All notable changes and phase deliverables for the **Incident Response & Root Cause Post-Mortem Agent** are documented in this file.

## [0.1.0] - 2026-10-24

### Phase 1: Brainstorm and Requirements (`docs/requirements.md`)
- Documented the 30-minute human on-call incident workflow and defined the exact automation vs. human-gated responsibility boundary.
- Defined primary (`On-Call Engineer`), secondary (`Engineering Manager`), and tertiary (`SRE Lead`) user personas.
- Specified input schemas for PagerDuty V3 webhooks (`trigger`, `acknowledge`, `resolve`), Slack slash commands (`/triage`), app mentions, interactive approval buttons, and Prometheus Alertmanager webhooks.
- Defined read-only adapter interfaces for Logs, Metrics, Deploy/Change History, Service Topology, and Runbooks/Post-Mortems.
- Established 7 non-functional requirements (read-only default, human action gate, PII/secret redaction, prompt-injection defense, LLM call tracing, graceful degradation, and multi-provider retry/fallback) and 12 testable acceptance criteria.

### Phase 2: Architecture Design (`docs/architecture.md`, `config/models.yaml`, `config/thresholds.yaml`)
- Designed the LangGraph supervisor-and-sub-agents architecture with tiered model routing:
  - `supervisor`: Groq `llama-3.1-8b-instant` (131k context) -> fallback Gemini `gemini-3.5-flash-lite` (1M context).
  - `core`: Groq `llama-3.3-70b-versatile` (131k context) -> fallback Gemini `gemini-3.8-flash` (1M context).
  - `long_context`: Gemini `gemini-3.8-flash` (1M context) routed dynamically when log bundles exceed `24,000` tokens.
- Specified all 11 typed sub-agent tools (`triage_intake`, `log_analyst`, `metrics_analyst`, `change_correlator`, `topology_mapper`, `runbook_retriever`, `hypothesis_ranker`, `remediation_proposer`, `timeline_builder`, `postmortem_writer`, `diagram_generator`) with timeouts and token budgets in `config/models.yaml`.
- Externalized operational thresholds, retry policies, and safety rules in `config/thresholds.yaml`.

### Phase 3: Core Development (`src/incident_agent/`, `scripts/simulate_incident.py`)
- Implemented Pydantic v2 domain models and tool schemas (`src/incident_agent/schemas.py`).
- Built structured single-line JSON logging with per-incident `contextvars` binding (`src/incident_agent/logging_utils.py`).
- Implemented `get_chat_model(role)` and `ResilientTieredChatModel` (`src/incident_agent/models/factory.py`) with exponential backoff, jitter, automatic primary-to-secondary failover, and `LLMTracer` integration.
- Built the 5-scenario synthetic data library (`bad_deploy`, `dependency_timeout`, `db_pool_exhaustion`, `config_flag_misfire`, `traffic_spike`) and read-only mock adapters (`src/incident_agent/adapters/`).
- Implemented all 11 sub-agents as LangChain `StructuredTool`s (`src/incident_agent/subagents/`).
- Implemented defense-in-depth safety controls (`RedactionFilter`, `InjectionFilter`, `BudgetEnforcer`, `DataSourceAllowlist`, and `ActionGate` / `execute_approved_action` in `src/incident_agent/safety/`).
- Implemented thread-safe `SQLiteIncidentStore` behind the `IncidentStore` protocol (`src/incident_agent/persistence/store.py`).
- Built `IncidentSupervisor` LangGraph state machine (`src/incident_agent/supervisor/graph.py`) with parallel telemetry execution, iterative confidence loop (capped at 3 rounds), checkpointing, and crash recovery (`resume_incident`).
- Built FastAPI webhook & Slack endpoints (`src/incident_agent/main.py`) and end-to-end CLI simulator (`scripts/simulate_incident.py`).

### Phase 4: Architecture Diagrams (`docs/diagrams/`, `scripts/render_diagrams.py`)
- Created `scripts/render_diagrams.py` to deterministically generate all 6 Mermaid (`.mmd`) source files and rendered vector SVG (`.svg`) diagrams:
  1. `01_system_context` (`.mmd` & `.svg`)
  2. `02_component_architecture` (`.mmd` & `.svg`)
  3. `03_incident_lifecycle_sequence` (`.mmd` & `.svg`)
  4. `04_supervisor_control_flow` (`.mmd` & `.svg`)
  5. `05_model_tier_routing` (`.mmd` & `.svg`)
  6. `06_deployment_topology` (`.mmd` & `.svg`)

### Phase 5: Testing, Evaluation & Quality Gates (`tests/`)
- Added comprehensive unit tests (`tests/unit/test_unit_suite.py`) covering all 5 scenarios across all 5 adapters, PII/secret redaction, prompt-injection stripping, mandatory human approval gating, step/token budgets, and simulated HTTP `429`/`503` provider fallback.
- Added integration tests (`tests/integration/test_integration_suite.py`) covering full end-to-end scenario runs, graceful degradation on adapter failure, >24k token long-context routing, Slack/PagerDuty client retries against a mock HTTP server, mid-investigation crash & checkpoint resume, and FastAPI webhook smoke tests.
- Created a 10-incident labeled golden dataset (`tests/fixtures/golden_incidents.json`) and evaluation harness (`tests/eval/test_eval_suite.py`) measuring Top-1 accuracy, Top-3 accuracy, evidence citation validity, proposal safety, mean time to triage, and cost per incident.

### Phase 6: Documentation & Handoff (`README.md`, `docs/postmortem-example.md`, `CHANGELOG.md`)
- Generated sample blameless post-mortem (`docs/postmortem-example.md`) from the `bad_deploy` simulation.
- Completed comprehensive setup, simulator, testing, evaluation, and extensibility documentation in `README.md`.
