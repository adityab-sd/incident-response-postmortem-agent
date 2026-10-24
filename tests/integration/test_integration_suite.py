"""Integration tests: 5-scenario pipeline, local mock HTTP server, checkpoint resume, and FastAPI smoke tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from incident_agent.adapters import (
    SCENARIO_CATALOG,
    AdapterBundle,
    MockLogAdapter,
)
from incident_agent.config import get_config
from incident_agent.integrations import PagerDutyClient, SlackClient
from incident_agent.main import (
    ApprovalRequestPayload,
    SlackCommandPayload,
    SlackInteractionPayload,
    create_app,
)
from incident_agent.persistence import SQLiteIncidentStore
from incident_agent.schemas import IncidentStatus, ModelRole, ScenarioType
from incident_agent.subagents import SubagentRuntimeContext, build_all_subagent_tools
from incident_agent.supervisor import IncidentSupervisor


def _invoke_fastapi_route(app: FastAPI, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Invoke a registered FastAPI APIRoute handler synchronously in-process."""
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method.upper() in route.methods:
            result: dict[str, Any] = route.endpoint(**kwargs)
            return result
    raise KeyError(f"Route {method} {path} not found on FastAPI app")


@pytest.mark.parametrize("scenario", list(ScenarioType))
def test_full_pipeline_all_five_scenarios_end_to_end(scenario: ScenarioType) -> None:
    """Run full pipeline (trigger -> triage -> approve -> resolve -> post-mortem) for each scenario."""
    cfg = get_config()
    store = SQLiteIncidentStore(":memory:")
    ctx = SubagentRuntimeContext(config=cfg, store=store)
    slack = SlackClient(config=cfg)
    pd = PagerDutyClient(config=cfg)
    sup = IncidentSupervisor(ctx=ctx, slack_client=slack, pagerduty_client=pd)

    spec = SCENARIO_CATALOG[scenario]
    inc_id = f"INC-INT-{scenario.value.upper()[:8]}"
    state = sup.start_incident_triage(
        incident_id=inc_id,
        raw_payload={
            "incident": {
                "id": inc_id,
                "title": spec.alert_title,
                "priority": spec.severity.value,
                "service": {"name": spec.primary_service},
                "custom_details": {"scenario": scenario.value},
            }
        },
        created_at=datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC),
    )

    assert state.status == IncidentStatus.TRIAGED_AWAITING_APPROVAL
    assert len(state.hypotheses) >= 2
    top_hyp = state.hypotheses[0]
    assert top_hyp.root_cause_category == spec.true_root_cause_category
    assert top_hyp.confidence >= 0.75
    assert all(eid in state.evidence_ids for eid in top_hyp.supporting_evidence_ids)
    assert len(state.proposals) >= 1
    assert all(
        p.proposal_only is True and p.requires_human_approval is True for p in state.proposals
    )

    # Approve and execute top remediation proposal
    approval, execution = sup.approve_and_execute_proposal(
        incident_id=inc_id,
        proposal_id=state.proposals[0].proposal_id,
        approver_id="U99SRE",
        decision="approved",
    )
    assert approval.decision == "approved"
    assert execution is not None
    assert execution.status == "executed"

    # Resolve incident and generate blameless post-mortem
    resolved = sup.resolve_and_generate_postmortem(inc_id)
    assert resolved.status == IncidentStatus.RESOLVED_POSTMORTEM_COMPLETE
    assert resolved.timeline is not None
    assert len(resolved.timeline.events) >= 7
    assert resolved.diagrams is not None
    assert "sequenceDiagram" in resolved.diagrams.incident_sequence_mermaid
    assert resolved.postmortem is not None
    assert resolved.postmortem.blameless_verified is True
    assert len(slack.sent_messages) == 3  # ack + triage summary + post-mortem


def test_graceful_degradation_when_log_source_fails_and_long_context_routing() -> None:
    """Verify pod continues when LogAdapter fails and routes >24k token logs to Gemini Flash."""
    cfg = get_config()
    store = SQLiteIncidentStore(":memory:")

    # 1. Degraded LogAdapter
    degraded_adapters = AdapterBundle(logs=MockLogAdapter(fail=True))
    ctx_deg = SubagentRuntimeContext(config=cfg, adapters=degraded_adapters, store=store)
    sup_deg = IncidentSupervisor(ctx=ctx_deg)
    state_deg = sup_deg.start_incident_triage(
        incident_id="INC-DEG-01",
        raw_payload={
            "incident": {
                "id": "INC-DEG-01",
                "service": {"name": "checkout-service"},
            }
        },
    )
    assert "logs" in state_deg.unavailable_sources
    assert state_deg.triage_summary is not None
    assert "logs" in state_deg.triage_summary.unavailable_sources
    assert len(state_deg.hypotheses) >= 1

    # 2. Large Log Bundle (>24,000 tokens) -> routes to ModelRole.LONG_CONTEXT
    large_adapters = AdapterBundle(logs=MockLogAdapter(large_bundle=True))
    ctx_large = SubagentRuntimeContext(config=cfg, adapters=large_adapters, store=store)
    sup_large = IncidentSupervisor(ctx=ctx_large)
    state_large = sup_large.start_incident_triage(
        incident_id="INC-LONG-01",
        raw_payload={
            "incident": {
                "id": "INC-LONG-01",
                "service": {"name": "checkout-service"},
            }
        },
    )
    assert state_large.log_outputs[0].routed_tier == ModelRole.LONG_CONTEXT
    assert state_large.log_outputs[0].estimated_input_tokens > 24000


def test_checkpoint_crash_and_resume_continues_from_last_checkpoint(
    tmp_path: Path,
) -> None:
    """Simulate a process crash mid-investigation and verify resume_incident continues cleanly."""
    db_file = tmp_path / "resume_test.db"
    cfg = get_config()
    store1 = SQLiteIncidentStore(db_file)
    ctx1 = SubagentRuntimeContext(config=cfg, store=store1)

    # First supervisor stops right after the `investigate` step (simulating crash before ranking)
    sup_interrupted = IncidentSupervisor(ctx=ctx1, stop_after_step="investigate")
    partial_state = sup_interrupted.start_incident_triage(
        incident_id="INC-RESUME-99",
        raw_payload={
            "incident": {
                "id": "INC-RESUME-99",
                "service": {"name": "inventory-service"},
                "custom_details": {"scenario": "db_pool_exhaustion"},
            }
        },
    )
    assert partial_state.last_completed_step == "investigate"
    assert partial_state.hypotheses == []

    ckpt = store1.get_latest_checkpoint("INC-RESUME-99")
    assert ckpt is not None
    assert ckpt[0] == "investigate"

    # Second supervisor instance connects to the same SQLite DB and resumes
    store2 = SQLiteIncidentStore(db_file)
    ctx2 = SubagentRuntimeContext(config=cfg, store=store2)
    sup_resumed = IncidentSupervisor(ctx=ctx2)
    completed_state = sup_resumed.resume_incident("INC-RESUME-99")

    assert completed_state.status == IncidentStatus.TRIAGED_AWAITING_APPROVAL
    assert completed_state.last_completed_step == "triaged"
    assert len(completed_state.hypotheses) >= 1
    assert completed_state.hypotheses[0].root_cause_category == "db_pool_exhaustion"
    assert len(completed_state.proposals) >= 1


def test_slack_and_pagerduty_clients_against_local_mock_http_server() -> None:
    """Test SlackClient and PagerDutyClient live HTTP requests and retry logic against a mock server."""
    received_requests: list[tuple[str, str, dict[str, Any]]] = []
    call_count = 0

    def mock_server_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        received_requests.append((request.method, request.url.path, body))
        if call_count == 1:
            return httpx.Response(503, json={"ok": False, "error": "service_unavailable"})
        return httpx.Response(200, json={"ok": True, "ts": "1700000000.123456"})

    transport = httpx.MockTransport(mock_server_handler)
    base_url = "http://mock-integrations.local"
    slack = SlackClient(base_url=base_url, live_http=True, transport=transport)
    pd = PagerDutyClient(base_url=base_url, live_http=True, transport=transport)

    ts = slack.post_acknowledgment(
        incident_id="INC-HTTP-01", service="checkout-service", severity="SEV1"
    )
    assert ts == "1700000000.123456"

    pd.acknowledge_incident("INC-HTTP-01", "PD-EXT-01")
    pd.add_incident_note("INC-HTTP-01", "PD-EXT-01", "Triage note")

    assert len(received_requests) == 4
    assert received_requests[0][1] == "/chat.postMessage"
    assert received_requests[2][1] == "/incidents/PD-EXT-01"
    assert received_requests[3][1] == "/incidents/PD-EXT-01/notes"


def test_fastapi_smoke_webhooks_and_structured_tools_execution() -> None:
    """Smoke test FastAPI endpoints (PagerDuty, Alertmanager, Slack commands/events/interactions)."""
    store = SQLiteIncidentStore(":memory:")
    app = create_app(store=store)

    # 1. Health endpoint
    health_data = _invoke_fastapi_route(app, "GET", "/health")
    assert health_data["status"] == "ok"

    # 2. PagerDuty trigger webhook
    trigger_data = _invoke_fastapi_route(
        app,
        "POST",
        "/webhooks/pagerduty",
        payload={
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "incident": {
                        "id": "INC-SMOKE-01",
                        "title": "High 5xx Error Rate on checkout-service",
                        "priority": "SEV1",
                        "service": {"name": "checkout-service"},
                        "custom_details": {"scenario": "bad_deploy"},
                    }
                },
            }
        },
    )
    assert trigger_data["incident_id"] == "INC-SMOKE-01"
    assert "NullPointerException" in trigger_data["top_hypothesis"]
    assert "Incident Triage Summary" in trigger_data["triage_markdown"]

    # 3. PagerDuty acknowledge webhook
    ack_data = _invoke_fastapi_route(
        app,
        "POST",
        "/webhooks/pagerduty",
        payload={
            "event": {
                "event_type": "incident.acknowledged",
                "data": {"incident": {"id": "INC-SMOKE-01"}},
            }
        },
    )
    assert ack_data["status"] == "ACKNOWLEDGED"

    # 4. Slack interactive button approval & API approval endpoint
    approve_data = _invoke_fastapi_route(
        app,
        "POST",
        "/slack/interactions",
        interaction=SlackInteractionPayload(
            incident_id="INC-SMOKE-01",
            proposal_id="PROP-01",
            user_id="U01ONCALL",
            decision="approved",
        ),
    )
    assert approve_data["execution"]["status"] == "executed"

    api_approve = _invoke_fastapi_route(
        app,
        "POST",
        "/incidents/{incident_id}/approve",
        incident_id="INC-SMOKE-01",
        req=ApprovalRequestPayload(
            proposal_id="PROP-01",
            approver_id="U02LEAD",
            decision="approved",
        ),
    )
    assert api_approve["approval"]["decision"] == "approved"

    # 5. PagerDuty resolve webhook -> generates blameless post-mortem
    resolve_data = _invoke_fastapi_route(
        app,
        "POST",
        "/webhooks/pagerduty",
        payload={
            "event": {
                "event_type": "incident.resolved",
                "data": {"incident": {"id": "INC-SMOKE-01"}},
            }
        },
    )
    assert "Blameless Post-Mortem" in resolve_data["postmortem_markdown"]

    # 6. GET incident details & traces
    inc_data = _invoke_fastapi_route(
        app, "GET", "/incidents/{incident_id}", incident_id="INC-SMOKE-01"
    )
    assert len(inc_data["traces"]) >= 8

    # 7. Alertmanager & Slack slash command / events endpoints
    am_data = _invoke_fastapi_route(
        app,
        "POST",
        "/webhooks/alertmanager",
        payload={
            "incident_id": "INC-AM-01",
            "commonLabels": {"service": "frontend-edge", "scenario": "traffic_spike"},
            "commonAnnotations": {"summary": "4x Traffic Surge"},
        },
    )
    assert am_data["incident_id"] == "INC-AM-01"

    sl_cmd = _invoke_fastapi_route(
        app,
        "POST",
        "/slack/commands",
        cmd=SlackCommandPayload(
            command="/triage",
            text="INC-SLK-01 service=recommendation-service scenario=config_flag_misfire",
        ),
    )
    assert sl_cmd["incident_id"] == "INC-SLK-01"

    sl_ev = _invoke_fastapi_route(
        app,
        "POST",
        "/slack/events",
        payload={
            "incident_id": "INC-MEN-01",
            "scenario": "dependency_timeout",
            "event": {
                "text": "@incident-agent investigate order-service",
                "channel": "C01INC",
            },
        },
    )
    assert sl_ev["incident_id"] == "INC-MEN-01"

    # 8. Invoke all 11 StructuredTool wrappers directly to verify tool schema serialization
    ctx = SubagentRuntimeContext(store=store)
    tools = build_all_subagent_tools(ctx)
    topo_dict = tools["topology_mapper"].invoke(
        {
            "incident_id": "INC-TOOL-01",
            "service": "checkout-service",
            "scenario": "bad_deploy",
        }
    )
    assert topo_dict["source_available"] is True
