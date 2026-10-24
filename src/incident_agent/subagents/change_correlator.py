"""`change_correlator` sub-agent tool (Tier: core)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.adapters.base import AdapterUnavailableError
from incident_agent.logging_utils import get_logger, log_event, set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.schemas import (
    ChangeCorrelatorInput,
    ChangeCorrelatorOutput,
    ChangeEvent,
    EvidenceRecord,
    ModelRole,
)
from incident_agent.subagents.context import SubagentRuntimeContext

logger = get_logger("subagents.change_correlator")


def run_change_correlator(
    inp: ChangeCorrelatorInput,
    ctx: SubagentRuntimeContext,
) -> ChangeCorrelatorOutput:
    """Find deploys, config changes, and flag flips near onset time and score correlation."""
    token = set_incident_context(inp.incident_id)
    try:
        start_time = inp.onset_time - timedelta(minutes=inp.lookback_minutes)
        end_time = inp.onset_time + timedelta(minutes=15)
        try:
            raw_changes = ctx.adapters.changes.get_recent_changes(
                inp.services,
                start_time,
                end_time,
                scenario=inp.scenario,
            )
        except AdapterUnavailableError as exc:
            log_event(
                logger,
                logging.WARNING,
                "Change adapter unavailable; degrading gracefully",
                incident_id=inp.incident_id,
                error=str(exc),
            )
            return ChangeCorrelatorOutput(
                source_available=False,
                error_message=str(exc),
            )

        sanitized_changes: list[ChangeEvent] = []
        evidence_ids: list[str] = []
        best_change: ChangeEvent | None = None
        best_score = 0.0

        for chg in raw_changes:
            clean_summary, _ = ctx.sanitize_and_redact(chg.summary, incident_id=inp.incident_id)
            clean_chg = ChangeEvent(
                evidence_id=chg.evidence_id,
                change_id=chg.change_id,
                service=chg.service,
                change_type=chg.change_type,
                version_or_key=chg.version_or_key,
                author_role=chg.author_role,
                timestamp=chg.timestamp,
                summary=clean_summary,
                rollback_command=chg.rollback_command,
            )
            sanitized_changes.append(clean_chg)
            evidence_ids.append(clean_chg.evidence_id)
            ctx.record_evidence(
                EvidenceRecord(
                    evidence_id=clean_chg.evidence_id,
                    incident_id=inp.incident_id,
                    source_type="deploys",
                    service=clean_chg.service,
                    timestamp=clean_chg.timestamp,
                    summary=f"[{clean_chg.change_type}] {clean_chg.version_or_key}: {clean_chg.summary}",
                    details=clean_chg.model_dump(mode="json"),
                )
            )

            delta_mins = abs((inp.onset_time - clean_chg.timestamp).total_seconds()) / 60.0
            score = max(0.1, round(1.0 - (delta_mins / max(inp.lookback_minutes, 1)), 2))
            if score > best_score:
                best_score = score
                best_change = clean_chg

        if best_change is not None:
            reasoning = (
                f"Change {best_change.change_id} ({best_change.change_type}: {best_change.version_or_key}) "
                f"on {best_change.service} occurred at {best_change.timestamp.isoformat()}, "
                f"tightly preceding anomaly onset ({inp.onset_time.isoformat()}) with correlation={best_score:.2f}."
            )
        else:
            reasoning = (
                f"Zero deployments, config changes, or feature flag flips detected within "
                f"{inp.lookback_minutes} minutes of onset on {inp.services}."
            )

        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="change_correlator",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(f"Correlate recent changes with incident onset: {reasoning}")

        return ChangeCorrelatorOutput(
            changes=sanitized_changes,
            most_suspect_change_id=best_change.change_id if best_change else None,
            most_suspect_evidence_id=best_change.evidence_id if best_change else None,
            correlation_score=best_score,
            correlation_reasoning=reasoning,
            evidence_ids=evidence_ids,
            source_available=True,
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_change_correlator_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `change_correlator` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = ChangeCorrelatorInput.model_validate(kwargs)
        return run_change_correlator(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="change_correlator",
        description="Find deploys, config changes, and feature flag flips near incident onset time.",
        args_schema=ChangeCorrelatorInput,
    )
