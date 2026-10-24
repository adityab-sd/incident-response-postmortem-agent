"""`postmortem_writer` sub-agent tool (Tier: core)."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.logging_utils import set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.reporting.builders import build_blameless_postmortem
from incident_agent.schemas import (
    ModelRole,
    PostMortemReport,
    PostMortemWriterInput,
)
from incident_agent.subagents.context import SubagentRuntimeContext


def run_postmortem_writer(
    inp: PostMortemWriterInput,
    ctx: SubagentRuntimeContext,
) -> PostMortemReport:
    """Produce a blameless post-mortem draft in Markdown from timeline and hypotheses."""
    token = set_incident_context(inp.incident_id)
    try:
        report = build_blameless_postmortem(inp)
        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="postmortem_writer",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(f"Draft blameless post-mortem for {inp.incident_id}: {report.title}")
        return report
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_postmortem_writer_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `postmortem_writer` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = PostMortemWriterInput.model_validate(kwargs)
        return run_postmortem_writer(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="postmortem_writer",
        description="Produce a blameless post-mortem draft in Markdown from the timeline and ranked hypotheses.",
        args_schema=PostMortemWriterInput,
    )
