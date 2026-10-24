"""All 11 sub-agents exposed as typed functions and LangChain `StructuredTool`s."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from incident_agent.subagents.change_correlator import (
    build_change_correlator_tool,
    run_change_correlator,
)
from incident_agent.subagents.context import SubagentRuntimeContext
from incident_agent.subagents.diagram_generator import (
    build_diagram_generator_tool,
    run_diagram_generator,
)
from incident_agent.subagents.hypothesis_ranker import (
    build_hypothesis_ranker_tool,
    run_hypothesis_ranker,
)
from incident_agent.subagents.log_analyst import build_log_analyst_tool, run_log_analyst
from incident_agent.subagents.metrics_analyst import (
    build_metrics_analyst_tool,
    run_metrics_analyst,
)
from incident_agent.subagents.postmortem_writer import (
    build_postmortem_writer_tool,
    run_postmortem_writer,
)
from incident_agent.subagents.remediation_proposer import (
    build_remediation_proposer_tool,
    run_remediation_proposer,
)
from incident_agent.subagents.runbook_retriever import (
    build_runbook_retriever_tool,
    run_runbook_retriever,
)
from incident_agent.subagents.timeline_builder import (
    build_timeline_builder_tool,
    run_timeline_builder,
)
from incident_agent.subagents.topology_mapper import (
    build_topology_mapper_tool,
    run_topology_mapper,
)
from incident_agent.subagents.triage_intake import (
    build_triage_intake_tool,
    run_triage_intake,
)


def build_all_subagent_tools(ctx: SubagentRuntimeContext) -> dict[str, StructuredTool]:
    """Return a dictionary of all 11 sub-agents wrapped as LangChain `StructuredTool`s."""
    tools = [
        build_triage_intake_tool(ctx),
        build_log_analyst_tool(ctx),
        build_metrics_analyst_tool(ctx),
        build_change_correlator_tool(ctx),
        build_topology_mapper_tool(ctx),
        build_runbook_retriever_tool(ctx),
        build_hypothesis_ranker_tool(ctx),
        build_remediation_proposer_tool(ctx),
        build_timeline_builder_tool(ctx),
        build_postmortem_writer_tool(ctx),
        build_diagram_generator_tool(ctx),
    ]
    return {tool.name: tool for tool in tools}


__all__ = [
    "SubagentRuntimeContext",
    "build_all_subagent_tools",
    "build_change_correlator_tool",
    "build_diagram_generator_tool",
    "build_hypothesis_ranker_tool",
    "build_log_analyst_tool",
    "build_metrics_analyst_tool",
    "build_postmortem_writer_tool",
    "build_remediation_proposer_tool",
    "build_runbook_retriever_tool",
    "build_timeline_builder_tool",
    "build_topology_mapper_tool",
    "build_triage_intake_tool",
    "run_change_correlator",
    "run_diagram_generator",
    "run_hypothesis_ranker",
    "run_log_analyst",
    "run_metrics_analyst",
    "run_postmortem_writer",
    "run_remediation_proposer",
    "run_runbook_retriever",
    "run_timeline_builder",
    "run_topology_mapper",
    "run_triage_intake",
]
