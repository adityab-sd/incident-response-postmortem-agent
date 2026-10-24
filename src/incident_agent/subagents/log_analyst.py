"""`log_analyst` sub-agent tool (Tier: core, with token-based routing to long_context)."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.adapters.base import AdapterUnavailableError
from incident_agent.logging_utils import get_logger, log_event, set_incident_context
from incident_agent.models.factory import get_chat_model, select_role_for_tokens
from incident_agent.schemas import (
    EvidenceRecord,
    LogAnalystInput,
    LogAnalystOutput,
    LogCluster,
    ModelRole,
)
from incident_agent.subagents.context import SubagentRuntimeContext
from incident_agent.tracing.tracer import estimate_token_count

logger = get_logger("subagents.log_analyst")


def run_log_analyst(
    inp: LogAnalystInput,
    ctx: SubagentRuntimeContext,
) -> LogAnalystOutput:
    """Query logs in window, sanitize/redact, cluster error signatures, and route by token count."""
    token = set_incident_context(inp.incident_id)
    try:
        try:
            raw_entries = ctx.adapters.logs.query_logs(
                inp.service,
                inp.window_start,
                inp.window_end,
                severity_filter=inp.severity_filter,
                scenario=inp.scenario,
            )
            raw_clusters = ctx.adapters.logs.get_error_clusters(
                inp.service,
                inp.window_start,
                inp.window_end,
                scenario=inp.scenario,
            )
        except AdapterUnavailableError as exc:
            log_event(
                logger,
                logging.WARNING,
                "Log adapter unavailable; degrading gracefully",
                incident_id=inp.incident_id,
                service=inp.service,
                error=str(exc),
            )
            return LogAnalystOutput(
                service=inp.service,
                source_available=False,
                error_message=str(exc),
            )

        sanitized_lines: list[str] = []
        injections_neutralized = 0
        for entry in raw_entries:
            clean_msg, inj_count = ctx.sanitize_and_redact(
                entry.message, incident_id=inp.incident_id
            )
            injections_neutralized += inj_count
            sanitized_lines.append(f"{entry.timestamp.isoformat()} [{entry.level}] {clean_msg}")

        sanitized_clusters: list[LogCluster] = []
        evidence_ids: list[str] = []
        for cluster in raw_clusters:
            clean_sig, inj_sig = ctx.sanitize_and_redact(
                cluster.signature, incident_id=inp.incident_id
            )
            clean_sample, inj_samp = ctx.sanitize_and_redact(
                cluster.sample_message, incident_id=inp.incident_id
            )
            injections_neutralized += inj_sig + inj_samp
            sc = LogCluster(
                evidence_id=cluster.evidence_id,
                service=cluster.service,
                signature=clean_sig,
                error_code=cluster.error_code,
                count=cluster.count,
                first_seen=cluster.first_seen,
                last_seen=cluster.last_seen,
                sample_message=clean_sample,
            )
            sanitized_clusters.append(sc)
            evidence_ids.append(sc.evidence_id)
            ctx.record_evidence(
                EvidenceRecord(
                    evidence_id=sc.evidence_id,
                    incident_id=inp.incident_id,
                    source_type="logs",
                    service=sc.service,
                    timestamp=sc.first_seen,
                    summary=f"[{sc.error_code}] {sc.signature} (count={sc.count})",
                    details=sc.model_dump(mode="json"),
                )
            )

        full_log_bundle = "\n".join(sanitized_lines)
        estimated_tokens = estimate_token_count(full_log_bundle)
        routed_tier = select_role_for_tokens(ModelRole.CORE, estimated_tokens, ctx.config)

        model = get_chat_model(
            routed_tier,
            subagent_name="log_analyst",
            estimated_tokens=estimated_tokens,
            tracer=ctx.tracer,
            config=ctx.config,
        )
        prompt = (
            f"Analyze error log clusters for service {inp.service}.\n"
            + ctx.injector.wrap_untrusted_block(
                full_log_bundle[:4000] if routed_tier == ModelRole.CORE else full_log_bundle,
                source_label="logs",
                incident_id=inp.incident_id,
            )
        )
        model.invoke_text(prompt)

        first_error = min(c.first_seen for c in sanitized_clusters) if sanitized_clusters else None
        total_errors = sum(c.count for c in sanitized_clusters)

        return LogAnalystOutput(
            service=inp.service,
            clusters=sanitized_clusters,
            first_error_at=first_error,
            total_error_count=total_errors,
            evidence_ids=evidence_ids,
            routed_tier=routed_tier,
            estimated_input_tokens=estimated_tokens,
            injections_neutralized=injections_neutralized,
            source_available=True,
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_log_analyst_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `log_analyst` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = LogAnalystInput.model_validate(kwargs)
        return run_log_analyst(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="log_analyst",
        description="Query logs in the incident window, cluster error signatures, and find first occurrence.",
        args_schema=LogAnalystInput,
    )
