"""`metrics_analyst` sub-agent tool (Tier: core)."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.adapters.base import AdapterUnavailableError
from incident_agent.logging_utils import get_logger, log_event, set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.schemas import (
    EvidenceRecord,
    MetricsAnalystInput,
    MetricsAnalystOutput,
    ModelRole,
)
from incident_agent.subagents.context import SubagentRuntimeContext

logger = get_logger("subagents.metrics_analyst")


def run_metrics_analyst(
    inp: MetricsAnalystInput,
    ctx: SubagentRuntimeContext,
) -> MetricsAnalystOutput:
    """Query service metrics, detect anomalies, and identify earliest anomaly onset time."""
    token = set_incident_context(inp.incident_id)
    try:
        try:
            anomalies = ctx.adapters.metrics.detect_anomalies(
                inp.service,
                inp.window_start,
                inp.window_end,
                scenario=inp.scenario,
            )
        except AdapterUnavailableError as exc:
            log_event(
                logger,
                logging.WARNING,
                "Metrics adapter unavailable; degrading gracefully",
                incident_id=inp.incident_id,
                service=inp.service,
                error=str(exc),
            )
            return MetricsAnalystOutput(
                service=inp.service,
                source_available=False,
                error_message=str(exc),
            )

        evidence_ids: list[str] = []
        peak_err = 0.0
        peak_lat = 0.0
        for anomaly in anomalies:
            evidence_ids.append(anomaly.evidence_id)
            if "error" in anomaly.metric_name or "timeout" in anomaly.metric_name:
                peak_err = max(peak_err, anomaly.peak_value)
            if "latency" in anomaly.metric_name:
                peak_lat = max(peak_lat, anomaly.peak_value)
            ctx.record_evidence(
                EvidenceRecord(
                    evidence_id=anomaly.evidence_id,
                    incident_id=inp.incident_id,
                    source_type="metrics",
                    service=anomaly.service,
                    timestamp=anomaly.onset_time,
                    summary=(
                        f"{anomaly.metric_name}: baseline={anomaly.baseline_value}{anomaly.unit} "
                        f"-> peak={anomaly.peak_value}{anomaly.unit} ({anomaly.deviation_factor}x)"
                    ),
                    details=anomaly.model_dump(mode="json"),
                )
            )

        earliest_onset = min((a.onset_time for a in anomalies), default=None)
        summary_text = "; ".join(a.description for a in anomalies)

        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="metrics_analyst",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(f"Analyze metric anomalies on {inp.service}: {summary_text}")

        return MetricsAnalystOutput(
            service=inp.service,
            anomalies=anomalies,
            earliest_onset=earliest_onset,
            peak_error_rate=peak_err,
            peak_latency_p99_ms=peak_lat,
            saturation_summary=summary_text,
            evidence_ids=evidence_ids,
            source_available=True,
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_metrics_analyst_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `metrics_analyst` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = MetricsAnalystInput.model_validate(kwargs)
        return run_metrics_analyst(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="metrics_analyst",
        description="Query metrics, detect anomalies, and identify earliest anomaly onset time.",
        args_schema=MetricsAnalystInput,
    )
