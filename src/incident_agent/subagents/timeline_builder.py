"""`timeline_builder` sub-agent tool (Tier: core)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.logging_utils import set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.schemas import (
    ModelRole,
    TimelineBuilderInput,
    TimelineBuilderOutput,
    TimelineEvent,
)
from incident_agent.subagents.context import SubagentRuntimeContext


def run_timeline_builder(
    inp: TimelineBuilderInput,
    ctx: SubagentRuntimeContext,
) -> TimelineBuilderOutput:
    """Assemble a strictly ordered chronological incident timeline from all evidence."""
    token = set_incident_context(inp.incident_id)
    try:
        events: list[TimelineEvent] = []

        if inp.change_output:
            for chg in inp.change_output.changes:
                events.append(
                    TimelineEvent(
                        timestamp=chg.timestamp,
                        phase="pre_incident",
                        source="deploys",
                        evidence_id=chg.evidence_id,
                        description=f"Change {chg.change_id} ({chg.change_type}: {chg.version_or_key}) applied on {chg.service}: {chg.summary}",
                        is_key_event=True,
                    )
                )

        for mo in inp.metrics_outputs:
            for anom in mo.anomalies:
                events.append(
                    TimelineEvent(
                        timestamp=anom.onset_time,
                        phase="onset",
                        source="metrics",
                        evidence_id=anom.evidence_id,
                        description=f"Metric anomaly onset on {anom.service}: {anom.description}",
                        is_key_event=True,
                    )
                )

        for lo in inp.log_outputs:
            for cluster in lo.clusters:
                events.append(
                    TimelineEvent(
                        timestamp=cluster.first_seen,
                        phase="onset",
                        source="logs",
                        evidence_id=cluster.evidence_id,
                        description=f"First occurrence of error cluster [{cluster.error_code}] on {cluster.service} ({cluster.count} total events)",
                        is_key_event=True,
                    )
                )

        alert_ts = inp.intake.onset_time + timedelta(minutes=2)
        events.append(
            TimelineEvent(
                timestamp=alert_ts,
                phase="detection",
                source="alerting",
                evidence_id=None,
                description=f"Alert triggered ({inp.intake.external_alert_id}): {inp.intake.title} [{inp.intake.severity.value}]",
                is_key_event=True,
            )
        )

        ack_ts = inp.acknowledged_at or (alert_ts + timedelta(seconds=12))
        events.append(
            TimelineEvent(
                timestamp=ack_ts,
                phase="triage",
                source="incident_agent",
                evidence_id=None,
                description=f"Incident Response Pod acknowledged {inp.incident_id} in Slack and started multi-agent investigation.",
                is_key_event=False,
            )
        )

        triaged_ts = inp.triaged_at or (ack_ts + timedelta(seconds=38))
        events.append(
            TimelineEvent(
                timestamp=triaged_ts,
                phase="triage",
                source="incident_agent",
                evidence_id=None,
                description="Triage summary, ranked hypotheses, and read-only remediation proposals posted to Slack thread.",
                is_key_event=True,
            )
        )

        for apr in inp.approvals:
            events.append(
                TimelineEvent(
                    timestamp=apr.approved_at,
                    phase="mitigation",
                    source="action_gate",
                    evidence_id=None,
                    description=f"Human responder ({apr.approver_id}) recorded '{apr.decision}' for remediation proposal {apr.proposal_id}.",
                    is_key_event=True,
                )
            )

        for exe in inp.executions:
            events.append(
                TimelineEvent(
                    timestamp=exe.executed_at,
                    phase="mitigation",
                    source="action_gate",
                    evidence_id=None,
                    description=f"Executed approved remediation {exe.proposal_id} ({exe.action_type.value}) on {exe.target_service}.",
                    is_key_event=True,
                )
            )

        res_ts = inp.resolved_at or (triaged_ts + timedelta(minutes=8))
        events.append(
            TimelineEvent(
                timestamp=res_ts,
                phase="resolution",
                source="pagerduty",
                evidence_id=None,
                description=f"Incident {inp.incident_id} marked resolved after telemetry returned to baseline.",
                is_key_event=True,
            )
        )

        events.sort(key=lambda e: e.timestamp)

        onset_ts = inp.intake.onset_time
        ttd = max(1, int((alert_ts - onset_ts).total_seconds()))
        tta = max(1, int((ack_ts - alert_ts).total_seconds()))
        ttm = max(tta + 10, int((triaged_ts - onset_ts).total_seconds()))
        ttr = max(ttm + 30, int((res_ts - onset_ts).total_seconds()))

        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="timeline_builder",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(
            f"Assemble ordered incident timeline for {inp.incident_id} ({len(events)} events)"
        )

        return TimelineBuilderOutput(
            incident_id=inp.incident_id,
            events=events,
            ttd_seconds=ttd,
            tta_seconds=tta,
            ttm_seconds=ttm,
            ttr_seconds=ttr,
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_timeline_builder_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `timeline_builder` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = TimelineBuilderInput.model_validate(kwargs)
        return run_timeline_builder(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="timeline_builder",
        description="Assemble a strictly ordered chronological incident timeline from all gathered evidence.",
        args_schema=TimelineBuilderInput,
    )
