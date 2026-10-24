"""FastAPI application exposing PagerDuty, Alertmanager, and Slack webhook endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from incident_agent.config import AppConfig, get_config
from incident_agent.integrations.clients import PagerDutyClient, SlackClient
from incident_agent.persistence.store import IncidentStore, SQLiteIncidentStore
from incident_agent.schemas import IncidentStatus
from incident_agent.subagents.context import SubagentRuntimeContext
from incident_agent.supervisor.graph import IncidentSupervisor


class ApprovalRequestPayload(BaseModel):
    """Request body for explicit human approval of a remediation proposal."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    approver_id: str
    decision: str = Field(default="approved")
    reason: str = Field(default="Approved via API")


class SlackCommandPayload(BaseModel):
    """Payload for `/triage` Slack slash command."""

    model_config = ConfigDict(extra="ignore")

    command: str = "/triage"
    text: str = ""
    user_id: str = "U01ONCALL"
    channel_id: str = "C01INCIDENTS"


class SlackInteractionPayload(BaseModel):
    """Payload for Slack interactive Approve/Reject button clicks."""

    model_config = ConfigDict(extra="ignore")

    incident_id: str
    proposal_id: str
    user_id: str
    decision: str = "approved"
    reason: str = "Clicked button in Slack thread"


def create_app(
    *,
    config: AppConfig | None = None,
    store: IncidentStore | None = None,
    supervisor: IncidentSupervisor | None = None,
) -> FastAPI:
    """Create and wire the FastAPI incident response application."""
    cfg = config or get_config()
    resolved_store = store or SQLiteIncidentStore(cfg.env.incident_db_path)
    ctx = SubagentRuntimeContext(config=cfg, store=resolved_store)
    slack_client = SlackClient(config=cfg)
    pd_client = PagerDutyClient(config=cfg)
    sup = supervisor or IncidentSupervisor(
        ctx=ctx,
        slack_client=slack_client,
        pagerduty_client=pd_client,
    )

    app = FastAPI(
        title="Incident Response & Root Cause Post-Mortem Agent",
        version="0.1.0",
        description="A DevOps triage pod that handles active alerts from Slack, PagerDuty, and Alertmanager.",
    )
    app.state.supervisor = sup
    app.state.store = resolved_store
    app.state.slack_client = slack_client
    app.state.pagerduty_client = pd_client

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "incident-response-agent"}

    @app.post("/webhooks/pagerduty")
    def pagerduty_webhook(payload: dict[str, Any]) -> dict[str, Any]:
        event_obj = payload.get("event", payload)
        event_type = str(
            (event_obj.get("event_type") if isinstance(event_obj, dict) else None)
            or payload.get("event_type")
            or "incident.triggered"
        )
        data_obj = (
            event_obj.get("data", payload.get("incident", payload))
            if isinstance(event_obj, dict)
            else payload
        )
        incident_obj = data_obj.get("incident", data_obj) if isinstance(data_obj, dict) else payload
        raw_id = str(
            (incident_obj.get("id") if isinstance(incident_obj, dict) else None)
            or payload.get("incident_id")
            or f"INC-{uuid.uuid4().hex[:8].upper()}"
        )
        incident_id = raw_id if raw_id.startswith("INC-") else f"INC-{raw_id}"

        if event_type == "incident.acknowledged":
            existing = resolved_store.get_state(incident_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
            existing.status = IncidentStatus.ACKNOWLEDGED
            existing.acknowledged_at = datetime.now(UTC)
            existing.updated_at = datetime.now(UTC)
            resolved_store.save_state(existing)
            return {
                "incident_id": incident_id,
                "status": existing.status.value,
                "event_type": event_type,
            }

        if event_type == "incident.resolved":
            existing = resolved_store.get_state(incident_id)
            if existing is None:
                # If not previously triaged, run triage first then resolve
                sup.start_incident_triage(
                    incident_id=incident_id,
                    raw_payload={"incident": incident_obj},
                    source_type="pagerduty",
                )
            resolved_state = sup.resolve_and_generate_postmortem(incident_id)
            return {
                "incident_id": incident_id,
                "status": resolved_state.status.value,
                "postmortem_title": resolved_state.postmortem.title
                if resolved_state.postmortem
                else "",
                "postmortem_markdown": (
                    resolved_state.postmortem.markdown_body if resolved_state.postmortem else ""
                ),
            }

        # Default: `incident.triggered`
        state = sup.start_incident_triage(
            incident_id=incident_id,
            raw_payload={"incident": incident_obj},
            source_type="pagerduty",
        )
        return {
            "incident_id": state.incident_id,
            "status": state.status.value,
            "service": state.intake.service if state.intake else "",
            "top_hypothesis": state.hypotheses[0].title if state.hypotheses else "",
            "top_confidence": state.hypotheses[0].confidence if state.hypotheses else 0.0,
            "proposals": [p.model_dump(mode="json") for p in state.proposals],
            "triage_markdown": (
                state.triage_summary.slack_markdown if state.triage_summary else ""
            ),
        }

    @app.post("/webhooks/alertmanager")
    def alertmanager_webhook(payload: dict[str, Any]) -> dict[str, Any]:
        common_labels = payload.get("commonLabels", {})
        if not isinstance(common_labels, dict):
            common_labels = {}
        common_ann = payload.get("commonAnnotations", {})
        if not isinstance(common_ann, dict):
            common_ann = {}

        incident_id = str(
            payload.get("incident_id")
            or common_labels.get("incident_id")
            or f"INC-AM-{uuid.uuid4().hex[:6].upper()}"
        )
        state = sup.start_incident_triage(
            incident_id=incident_id,
            raw_payload={
                "commonLabels": common_labels,
                "commonAnnotations": common_ann,
                "scenario": common_labels.get("scenario", ""),
            },
            source_type="alertmanager",
        )
        return {
            "incident_id": state.incident_id,
            "status": state.status.value,
            "service": state.intake.service if state.intake else "",
            "top_hypothesis": state.hypotheses[0].title if state.hypotheses else "",
            "triage_markdown": (
                state.triage_summary.slack_markdown if state.triage_summary else ""
            ),
        }

    @app.post("/slack/commands")
    def slack_slash_command(cmd: SlackCommandPayload) -> dict[str, Any]:
        tokens = cmd.text.strip().split()
        raw_id = tokens[0] if tokens else f"INC-SLK-{uuid.uuid4().hex[:6].upper()}"
        incident_id = raw_id if raw_id.startswith("INC-") else f"INC-{raw_id}"

        kv_args: dict[str, str] = {}
        for tok in tokens[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv_args[k.strip()] = v.strip()

        state = sup.start_incident_triage(
            incident_id=incident_id,
            raw_payload={
                "incident": {
                    "id": incident_id,
                    "service": {"name": kv_args.get("service", "")},
                    "title": cmd.text,
                    "custom_details": {"scenario": kv_args.get("scenario", "")},
                }
            },
            source_type="slack",
            slack_channel=cmd.channel_id,
        )
        return {
            "response_type": "in_channel",
            "incident_id": state.incident_id,
            "status": state.status.value,
            "text": state.triage_summary.slack_markdown if state.triage_summary else "",
        }

    @app.post("/slack/events")
    def slack_events(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        event_obj = payload.get("event", payload)
        text = str(event_obj.get("text", "")) if isinstance(event_obj, dict) else ""
        channel = (
            str(event_obj.get("channel", "C01INCIDENTS"))
            if isinstance(event_obj, dict)
            else "C01INCIDENTS"
        )
        incident_id = str(payload.get("incident_id") or f"INC-MEN-{uuid.uuid4().hex[:6].upper()}")
        state = sup.start_incident_triage(
            incident_id=incident_id,
            raw_payload={"text": text, "scenario": payload.get("scenario", "")},
            source_type="slack",
            slack_channel=channel,
        )
        return {
            "incident_id": state.incident_id,
            "status": state.status.value,
            "top_hypothesis": state.hypotheses[0].title if state.hypotheses else "",
        }

    @app.post("/slack/interactions")
    def slack_interactions(interaction: SlackInteractionPayload) -> dict[str, Any]:
        try:
            approval, execution = sup.approve_and_execute_proposal(
                incident_id=interaction.incident_id,
                proposal_id=interaction.proposal_id,
                approver_id=interaction.user_id,
                decision=interaction.decision,
                reason=interaction.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return {
            "incident_id": interaction.incident_id,
            "proposal_id": interaction.proposal_id,
            "approval": approval.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json") if execution else None,
        }

    @app.post("/incidents/{incident_id}/approve")
    def approve_incident_proposal(incident_id: str, req: ApprovalRequestPayload) -> dict[str, Any]:
        try:
            approval, execution = sup.approve_and_execute_proposal(
                incident_id=incident_id,
                proposal_id=req.proposal_id,
                approver_id=req.approver_id,
                decision=req.decision,
                reason=req.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return {
            "incident_id": incident_id,
            "approval": approval.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json") if execution else None,
        }

    @app.get("/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict[str, Any]:
        state = resolved_store.get_state(incident_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
        traces = resolved_store.list_traces(incident_id)
        return {
            "incident": state.model_dump(mode="json"),
            "traces": [t.model_dump(mode="json") for t in traces],
        }

    return app


app = create_app()
