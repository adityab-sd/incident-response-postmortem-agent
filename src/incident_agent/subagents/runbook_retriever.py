"""`runbook_retriever` sub-agent tool (Tier: core)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.adapters.base import AdapterUnavailableError
from incident_agent.logging_utils import get_logger, log_event, set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.schemas import (
    EvidenceRecord,
    ModelRole,
    RunbookDocument,
    RunbookRetrieverInput,
    RunbookRetrieverOutput,
)
from incident_agent.subagents.context import SubagentRuntimeContext

logger = get_logger("subagents.runbook_retriever")


def run_runbook_retriever(
    inp: RunbookRetrieverInput,
    ctx: SubagentRuntimeContext,
) -> RunbookRetrieverOutput:
    """Retrieve operational runbooks and similar past post-mortems."""
    token = set_incident_context(inp.incident_id)
    try:
        try:
            raw_runbooks = ctx.adapters.runbooks.search_runbooks(
                inp.service, inp.symptoms, scenario=inp.scenario
            )
            past_pms = ctx.adapters.runbooks.search_past_postmortems(
                inp.service, inp.symptoms, scenario=inp.scenario
            )
        except AdapterUnavailableError as exc:
            log_event(
                logger,
                logging.WARNING,
                "Runbook adapter unavailable; degrading gracefully",
                incident_id=inp.incident_id,
                service=inp.service,
                error=str(exc),
            )
            return RunbookRetrieverOutput(
                source_available=False,
                error_message=str(exc),
            )

        sanitized_runbooks: list[RunbookDocument] = []
        evidence_ids: list[str] = []
        for rb in raw_runbooks:
            clean_title, _ = ctx.sanitize_and_redact(rb.title, incident_id=inp.incident_id)
            clean_diag = [
                ctx.sanitize_and_redact(s, incident_id=inp.incident_id)[0]
                for s in rb.diagnostic_steps
            ]
            clean_rem = [
                ctx.sanitize_and_redact(s, incident_id=inp.incident_id)[0]
                for s in rb.safe_remediation_steps
            ]
            clean_rb = RunbookDocument(
                evidence_id=rb.evidence_id,
                runbook_id=rb.runbook_id,
                title=clean_title,
                service=rb.service,
                symptom_tags=list(rb.symptom_tags),
                diagnostic_steps=clean_diag,
                safe_remediation_steps=clean_rem,
            )
            sanitized_runbooks.append(clean_rb)
            evidence_ids.append(clean_rb.evidence_id)
            ctx.record_evidence(
                EvidenceRecord(
                    evidence_id=clean_rb.evidence_id,
                    incident_id=inp.incident_id,
                    source_type="runbooks",
                    service=clean_rb.service,
                    timestamp=datetime.now(UTC),
                    summary=f"Runbook {clean_rb.runbook_id}: {clean_rb.title}",
                    details=clean_rb.model_dump(mode="json"),
                )
            )

        for pm in past_pms:
            evidence_ids.append(pm.evidence_id)
            ctx.record_evidence(
                EvidenceRecord(
                    evidence_id=pm.evidence_id,
                    incident_id=inp.incident_id,
                    source_type="runbooks",
                    service=pm.service,
                    timestamp=datetime.now(UTC),
                    summary=f"Past Post-Mortem {pm.past_incident_id}: {pm.title}",
                    details=pm.model_dump(mode="json"),
                )
            )

        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="runbook_retriever",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(
            f"Synthesize runbooks for {inp.service}: {[r.runbook_id for r in sanitized_runbooks]}"
        )

        return RunbookRetrieverOutput(
            runbooks=sanitized_runbooks,
            past_postmortems=past_pms,
            evidence_ids=evidence_ids,
            source_available=True,
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_runbook_retriever_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `runbook_retriever` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = RunbookRetrieverInput.model_validate(kwargs)
        return run_runbook_retriever(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="runbook_retriever",
        description="Retrieve relevant operational runbooks and similar past post-mortems.",
        args_schema=RunbookRetrieverInput,
    )
