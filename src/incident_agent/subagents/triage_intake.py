"""`triage_intake` sub-agent tool (Tier: supervisor)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.adapters.scenarios import (
    SCENARIO_CATALOG,
    infer_scenario_from_service_or_text,
)
from incident_agent.logging_utils import set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.schemas import (
    ModelRole,
    ScenarioType,
    Severity,
    TriageIntakeInput,
    TriageIntakeOutput,
)
from incident_agent.subagents.context import SubagentRuntimeContext


def run_triage_intake(
    inp: TriageIntakeInput,
    ctx: SubagentRuntimeContext,
) -> TriageIntakeOutput:
    """Parse PagerDuty, Slack, or Alertmanager payload into normalized triage context."""
    token = set_incident_context(inp.incident_id)
    try:
        payload: dict[str, Any] = ctx.redactor.redact_mapping(inp.raw_payload)
        incident_obj = payload.get("incident", payload)
        custom_details = (
            incident_obj.get("custom_details", {}) if isinstance(incident_obj, dict) else {}
        )
        if not isinstance(custom_details, dict):
            custom_details = {}

        # Extract scenario hint if present, or infer from service/title
        raw_scenario = str(custom_details.get("scenario") or payload.get("scenario") or "").strip()
        service_val = ""
        if isinstance(incident_obj, dict):
            svc_field = incident_obj.get("service")
            if isinstance(svc_field, dict):
                service_val = str(svc_field.get("name") or svc_field.get("summary") or "")
            elif isinstance(svc_field, str):
                service_val = svc_field
        if not service_val:
            common_labels = payload.get("commonLabels", {})
            if isinstance(common_labels, dict):
                service_val = str(common_labels.get("service", ""))

        title_val = ""
        if isinstance(incident_obj, dict):
            title_val = str(
                incident_obj.get("title")
                or incident_obj.get("summary")
                or payload.get("text")
                or ""
            )
        if not title_val:
            common_ann = payload.get("commonAnnotations", {})
            if isinstance(common_ann, dict):
                title_val = str(common_ann.get("summary") or common_ann.get("description") or "")

        if raw_scenario in {s.value for s in ScenarioType}:
            scenario = ScenarioType(raw_scenario)
        else:
            scenario = infer_scenario_from_service_or_text(service_val, title_val)

        scenario_def = SCENARIO_CATALOG[scenario]
        resolved_service = service_val or scenario_def.primary_service
        resolved_title, _ = ctx.sanitize_and_redact(
            title_val or scenario_def.alert_title, incident_id=inp.incident_id
        )
        symptom_summary, _ = ctx.sanitize_and_redact(
            str(custom_details.get("symptom") or scenario_def.symptom_summary),
            incident_id=inp.incident_id,
        )

        raw_sev = str(
            (incident_obj.get("priority") if isinstance(incident_obj, dict) else None)
            or custom_details.get("severity")
            or scenario_def.severity.value
        ).upper()
        severity = (
            Severity(raw_sev) if raw_sev in {s.value for s in Severity} else scenario_def.severity
        )

        # Invoke supervisor tier LLM to trace intake parsing
        model = get_chat_model(
            ModelRole.SUPERVISOR,
            subagent_name="triage_intake",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        prompt = (
            "Extract structured incident intake fields from sanitized alert payload.\n"
            + ctx.injector.wrap_untrusted_block(
                f"service={resolved_service} severity={severity.value} title={resolved_title} symptom={symptom_summary}",
                source_label=inp.source_type,
                incident_id=inp.incident_id,
            )
        )
        model.invoke_text(prompt)

        lookback = ctx.config.thresholds.investigation.telemetry_lookback_minutes
        lookahead = ctx.config.thresholds.investigation.telemetry_lookahead_minutes
        onset_time = inp.received_at - timedelta(minutes=12)

        ext_id = str(
            (incident_obj.get("id") if isinstance(incident_obj, dict) else None)
            or payload.get("external_id")
            or f"EXT-{inp.incident_id}"
        )

        return TriageIntakeOutput(
            incident_id=inp.incident_id,
            external_alert_id=ext_id,
            service=resolved_service,
            severity=severity,
            environment=str(custom_details.get("environment", "production")),
            title=resolved_title,
            symptom_summary=symptom_summary,
            symptom_tags=list(scenario_def.symptom_tags),
            scenario_hint=scenario,
            onset_time=onset_time,
            window_start=inp.received_at - timedelta(minutes=lookback),
            window_end=inp.received_at + timedelta(minutes=lookahead),
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_triage_intake_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `triage_intake` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = TriageIntakeInput.model_validate(kwargs)
        return run_triage_intake(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="triage_intake",
        description="Parse incoming alert payload, extract service, severity, time window, and symptoms.",
        args_schema=TriageIntakeInput,
    )
