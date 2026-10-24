"""`topology_mapper` sub-agent tool (Tier: core)."""

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
    TopologyMapperInput,
    TopologyMapperOutput,
)
from incident_agent.subagents.context import SubagentRuntimeContext

logger = get_logger("subagents.topology_mapper")


def run_topology_mapper(
    inp: TopologyMapperInput,
    ctx: SubagentRuntimeContext,
) -> TopologyMapperOutput:
    """Given the affected service, map upstream callers and downstream dependencies."""
    token = set_incident_context(inp.incident_id)
    try:
        try:
            topology = ctx.adapters.topology.get_service_topology(
                inp.service, scenario=inp.scenario
            )
        except AdapterUnavailableError as exc:
            log_event(
                logger,
                logging.WARNING,
                "Topology adapter unavailable; degrading gracefully",
                incident_id=inp.incident_id,
                service=inp.service,
                error=str(exc),
            )
            return TopologyMapperOutput(
                source_available=False,
                error_message=str(exc),
            )

        downstream_names = [d.service_name for d in topology.downstream_dependencies]
        critical_deps = [
            d.service_name for d in topology.downstream_dependencies if d.criticality == "critical"
        ]
        ctx.record_evidence(
            EvidenceRecord(
                evidence_id=topology.evidence_id,
                incident_id=inp.incident_id,
                source_type="topology",
                service=topology.service,
                timestamp=datetime.now(UTC),
                summary=(
                    f"Topology for {topology.service}: upstream={topology.upstream_callers}, "
                    f"critical_downstream={critical_deps}"
                ),
                details=topology.model_dump(mode="json"),
            )
        )

        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="topology_mapper",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(
            f"Map service dependencies for {inp.service}: downstream={downstream_names}"
        )

        return TopologyMapperOutput(
            topology=topology,
            upstream_callers=list(topology.upstream_callers),
            downstream_services=downstream_names,
            critical_dependencies=critical_deps,
            evidence_ids=[topology.evidence_id],
            source_available=True,
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_topology_mapper_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `topology_mapper` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = TopologyMapperInput.model_validate(kwargs)
        return run_topology_mapper(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="topology_mapper",
        description="Given the affected service, list upstream callers and downstream dependencies to check.",
        args_schema=TopologyMapperInput,
    )
