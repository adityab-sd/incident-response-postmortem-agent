"""`diagram_generator` sub-agent tool (Tier: core)."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.logging_utils import set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.reporting.builders import generate_incident_diagrams
from incident_agent.schemas import (
    DiagramGeneratorInput,
    DiagramGeneratorOutput,
    ModelRole,
)
from incident_agent.subagents.context import SubagentRuntimeContext


def run_diagram_generator(
    inp: DiagramGeneratorInput,
    ctx: SubagentRuntimeContext,
) -> DiagramGeneratorOutput:
    """Produce Mermaid sequence and blast-radius diagrams for an incident."""
    token = set_incident_context(inp.incident_id)
    try:
        output = generate_incident_diagrams(inp)
        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="diagram_generator",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(f"Generate Mermaid diagrams for incident {inp.incident_id}")
        return output
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_diagram_generator_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `diagram_generator` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = DiagramGeneratorInput.model_validate(kwargs)
        return run_diagram_generator(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="diagram_generator",
        description="Produce Mermaid sequence and blast-radius diagrams for the incident post-mortem.",
        args_schema=DiagramGeneratorInput,
    )
