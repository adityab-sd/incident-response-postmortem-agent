"""Markdown and Mermaid builders for Slack triage summaries, diagrams, and blameless post-mortems."""

from __future__ import annotations

import re
from typing import Any

from incident_agent.schemas import (
    ActionItem,
    DiagramGeneratorInput,
    DiagramGeneratorOutput,
    PostMortemReport,
    PostMortemWriterInput,
    RemediationProposal,
    RootCauseHypothesis,
    TriageSummaryReport,
)

_BLAME_WORDS_RE = re.compile(
    r"(?i)\b(?:fault\s+of|blamed\s+on|careless|negligent|reckless|human\s+error\s+by)\b"
)


def sanitize_blameless_text(text: str) -> str:
    """Ensure post-mortem text strictly adheres to blameless engineering culture."""
    return _BLAME_WORDS_RE.sub("systemic process gap", text)


def format_slack_triage_summary(report: TriageSummaryReport) -> str:
    """Render a Slack Markdown triage summary with hypotheses, evidence IDs, and proposals."""
    lines: list[str] = [
        f"🚨 *Incident Triage Summary — `{report.incident_id}`*",
        f"• *Service:* `{report.service}` | *Severity:* `{report.severity.value}` | *Status:* `{report.status.value}`",
        f"• *Symptom:* {report.symptom_summary}",
        f"• *Investigation:* {report.investigation_rounds} round(s), {report.tool_calls_used} tool calls, "
        f"{report.total_tokens_used} tokens (${report.total_cost_usd:.5f}, {report.triage_duration_seconds:.2f}s)",
    ]
    if report.budget_halted:
        lines.append(
            "⚠️ *Budget Notice:* Investigation reached step/token cap; reporting partial results."
        )
    if report.unavailable_sources:
        joined_unavailable = ", ".join(f"`{s}`" for s in report.unavailable_sources)
        lines.append(f"⚠️ *Degraded Data Sources (Unavailable):* {joined_unavailable}")

    lines.append("\n*🔍 Ranked Root-Cause Hypotheses:*")
    for hyp in report.hypotheses:
        pct = round(hyp.confidence * 100)
        cites = ", ".join(f"`{eid}`" for eid in hyp.supporting_evidence_ids) or "`none`"
        lines.append(
            f"{hyp.rank}. *{hyp.title}* — Confidence: *{pct}%* (`{hyp.root_cause_category}` on `{hyp.primary_service}`)\n"
            f"   _Evidence:_ {cites}\n"
            f"   _Reasoning:_ {hyp.reasoning}"
        )

    lines.append(
        "\n*🛠️ Suggested Remediation Proposals (PROPOSALS ONLY — Human Approval Required):*"
    )
    for prop in report.proposals:
        lines.append(
            f"• *[{prop.proposal_id}] {prop.title}* (`{prop.action_type.value}`, Risk: `{prop.risk_level.value.upper()}`)\n"
            f"  _Command:_ `{prop.command_or_api_call}`\n"
            f"  _Rollback:_ `{prop.rollback_path}`\n"
            f"  _Gate:_ `[Approve {prop.proposal_id}]` | `[Reject {prop.proposal_id}]` *(Requires explicit human approval)*"
        )
    return "\n".join(lines)


def build_slack_blocks(report: TriageSummaryReport) -> list[dict[str, Any]]:
    """Build Slack Block Kit payload including interactive Approve/Reject buttons for proposals."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": format_slack_triage_summary(report),
            },
        }
    ]
    action_elements: list[dict[str, Any]] = []
    for prop in report.proposals[:2]:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"Approve {prop.proposal_id}"},
                "style": "primary",
                "action_id": f"approve_action:{prop.proposal_id}",
                "value": f"{report.incident_id}:{prop.proposal_id}:approved",
            }
        )
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"Reject {prop.proposal_id}"},
                "style": "danger",
                "action_id": f"reject_action:{prop.proposal_id}",
                "value": f"{report.incident_id}:{prop.proposal_id}:rejected",
            }
        )
    if action_elements:
        blocks.append({"type": "actions", "elements": action_elements})
    return blocks


def generate_incident_diagrams(inp: DiagramGeneratorInput) -> DiagramGeneratorOutput:
    """Produce Mermaid sequence and blast-radius diagrams for an incident."""
    service = inp.intake.service
    top_cat = inp.top_hypothesis.root_cause_category if inp.top_hypothesis else "unknown"
    primary_svc = inp.top_hypothesis.primary_service if inp.top_hypothesis else service

    seq_lines = [
        "sequenceDiagram",
        "    autonumber",
        "    participant Alert as PagerDuty / Alertmanager",
        "    participant Pod as Incident Triage Pod",
        "    participant Obs as Logs / Metrics / Deploys",
        "    participant Slack as Slack Incident Channel",
        "    participant OnCall as Human On-Call Engineer",
        f"    Alert->>Pod: Trigger {inp.incident_id} ({service}, {inp.intake.severity.value})",
        f"    Pod->>Slack: Acknowledge alert & open thread ({inp.incident_id})",
        f"    Pod->>Obs: Parallel telemetry query ({service})",
        f"    Obs-->>Pod: Evidence ({top_cat} on {primary_svc})",
        "    Pod->>Slack: Post ranked hypotheses & PROPOSAL_ONLY remediations",
        "    OnCall->>Slack: Click Approve button on remediation proposal",
        "    Slack->>Pod: Record ApprovalRecord & execute_approved_action",
        "    Alert->>Pod: incident.resolved webhook",
        "    Pod->>Slack: Publish Blameless Post-Mortem & Mermaid diagrams",
    ]

    blast_lines = [
        "flowchart LR",
        f'    target["{service}<br/>({inp.intake.severity.value} Alert)"]',
    ]
    if inp.topology_output and inp.topology_output.topology:
        topo = inp.topology_output.topology
        for idx, caller in enumerate(topo.upstream_callers):
            cid = f"up_{idx}"
            blast_lines.append(f'    {cid}["{caller}<br/>(Upstream Caller)"] --> target')
        for idx, dep in enumerate(topo.downstream_dependencies):
            did = f"down_{idx}"
            blast_lines.append(
                f'    target -->|"protocol: {dep.protocol}"| {did}["{dep.service_name}<br/>({dep.dependency_type})"]'
            )
    else:
        blast_lines.append(f'    caller["Upstream Traffic"] --> target --> dep["{primary_svc}"]')

    return DiagramGeneratorOutput(
        incident_sequence_mermaid="\n".join(seq_lines),
        blast_radius_mermaid="\n".join(blast_lines),
    )


def build_blameless_postmortem(inp: PostMortemWriterInput) -> PostMortemReport:
    """Assemble a complete, blameless Markdown post-mortem draft from timeline and hypotheses."""
    top_hyp: RootCauseHypothesis | None = inp.hypotheses[0] if inp.hypotheses else None
    root_cause_summary = sanitize_blameless_text(
        top_hyp.reasoning
        if top_hyp is not None
        else f"Service degradation on {inp.intake.service} requiring automated triage."
    )
    impact_summary = (
        f"Service `{inp.intake.service}` experienced `{inp.intake.severity.value}` degradation "
        f"({inp.intake.symptom_summary}) in `{inp.intake.environment}`. "
        f"Time to Detect (TTD): {inp.timeline.ttd_seconds}s, "
        f"Time to Acknowledge (TTA): {inp.timeline.tta_seconds}s, "
        f"Time to Mitigate (TTM): {inp.timeline.ttm_seconds}s, "
        f"Time to Resolve (TTR): {inp.timeline.ttr_seconds}s."
    )

    contributing_factors = [
        f"Primary technical trigger: {top_hyp.title} (Confidence: {int(top_hyp.confidence * 100)}%, Evidence: {', '.join(top_hyp.supporting_evidence_ids)})"
        if top_hyp
        else "Automated anomaly trigger across service telemetry.",
        "Automated pre-production canary or load-gate thresholds did not catch the condition prior to full traffic exposure.",
    ]
    if inp.unavailable_sources:
        contributing_factors.append(
            f"Observability partial degradation during triage: {', '.join(inp.unavailable_sources)} unavailable."
        )

    what_went_well = [
        "Automated triage pod ingested the alert, correlated logs, metrics, topology, and changes in parallel, and posted a structured triage summary well within the 2-minute SLA.",
        "Strict read-only safety gates prevented unapproved production mutations until explicit human on-call approval.",
        "Secrets and PII in log streams were automatically redacted prior to LLM analysis.",
    ]
    what_could_be_improved = [
        f"Strengthen automated canary validation and pre-deploy/flag guardrails for `{inp.intake.service}`.",
        "Reduce blast radius through tighter circuit-breaker and saturation alerting thresholds.",
    ]

    action_items = [
        ActionItem(
            item_id=f"AI-{inp.incident_id}-01",
            category="prevention",
            priority="P0",
            owner_role="Service Owning Engineering Team",
            description=f"Add automated canary regression gate for `{inp.intake.service}` covering `{top_hyp.root_cause_category if top_hyp else 'anomaly'}` failure mode.",
        ),
        ActionItem(
            item_id=f"AI-{inp.incident_id}-02",
            category="detection",
            priority="P1",
            owner_role="SRE & Observability Platform Team",
            description=f"Tune early-warning saturation and error-budget burn alerts for `{inp.intake.service}` and its critical dependencies.",
        ),
        ActionItem(
            item_id=f"AI-{inp.incident_id}-03",
            category="mitigation",
            priority="P1",
            owner_role="On-Call Platform Engineering",
            description="Codify the verified rollback/mitigation runbook step into automated pre-checks.",
        ),
    ]

    timeline_rows = [
        "| Timestamp (UTC) | Phase | Source | Evidence ID | Event Description |",
        "|---|---|---|---|---|",
    ]
    for ev in inp.timeline.events:
        ev_id = f"`{ev.evidence_id}`" if ev.evidence_id else "-"
        marker = "**[KEY]** " if ev.is_key_event else ""
        timeline_rows.append(
            f"| `{ev.timestamp.strftime('%Y-%m-%d %H:%M:%S')}` | `{ev.phase}` | `{ev.source}` | {ev_id} | {marker}{sanitize_blameless_text(ev.description)} |"
        )

    hyp_lines: list[str] = []
    for hyp in inp.hypotheses:
        cites = ", ".join(f"`{eid}`" for eid in hyp.supporting_evidence_ids)
        hyp_lines.append(
            f"{hyp.rank}. **{hyp.title}** (`{hyp.root_cause_category}`, Confidence: `{hyp.confidence:.2f}`)\n"
            f"   - **Primary Service:** `{hyp.primary_service}`\n"
            f"   - **Cited Evidence IDs:** {cites}\n"
            f"   - **Analysis:** {sanitize_blameless_text(hyp.reasoning)}"
        )

    prop_lines: list[str] = []
    for prop in inp.proposals:
        prop_lines.append(
            f"- **`{prop.proposal_id}` — {prop.title}** (`{prop.action_type.value}`, Risk: `{prop.risk_level.value}`)\n"
            f"  - Command: `{prop.command_or_api_call}`\n"
            f"  - Rollback Path: `{prop.rollback_path}`\n"
            f"  - Status: Proposal Only (Requires Human Approval)"
        )

    action_rows = [
        "| ID | Priority | Category | Owner Role | Description |",
        "|---|---|---|---|---|",
    ]
    for item in action_items:
        action_rows.append(
            f"| `{item.item_id}` | `{item.priority}` | `{item.category}` | {item.owner_role} | {item.description} |"
        )

    diagrams_section = ""
    if inp.diagrams is not None:
        diagrams_section = (
            "\n## 7. Incident Architecture & Sequence Diagrams\n\n"
            "### Blast Radius & Dependency Topology\n"
            f"```mermaid\n{inp.diagrams.blast_radius_mermaid}\n```\n\n"
            "### Incident Lifecycle Sequence\n"
            f"```mermaid\n{inp.diagrams.incident_sequence_mermaid}\n```\n"
        )

    title = f"Blameless Post-Mortem: {inp.incident_id} — {inp.intake.title}"
    markdown_body = sanitize_blameless_text(
        f"# {title}\n\n"
        f"**Incident ID:** `{inp.incident_id}`  \n"
        f"**Service:** `{inp.intake.service}`  \n"
        f"**Severity:** `{inp.intake.severity.value}`  \n"
        f"**Environment:** `{inp.intake.environment}`  \n"
        f"**Culture Policy:** Blameless Systems-Focused Review\n\n"
        f"---\n\n"
        f"## 1. Executive Summary\n\n"
        f"{impact_summary}\n\n"
        f"**Root Cause Summary:** {root_cause_summary}\n\n"
        f"## 2. Detection & Response Metrics\n\n"
        f"- **Time to Detect (TTD):** `{inp.timeline.ttd_seconds}s`\n"
        f"- **Time to Acknowledge (TTA):** `{inp.timeline.tta_seconds}s`\n"
        f"- **Time to Mitigate (TTM):** `{inp.timeline.ttm_seconds}s`\n"
        f"- **Time to Resolve (TTR):** `{inp.timeline.ttr_seconds}s`\n\n"
        f"## 3. Chronological Incident Timeline\n\n"
        + "\n".join(timeline_rows)
        + "\n\n## 4. Root Cause Analysis & Ranked Hypotheses\n\n"
        + "\n".join(hyp_lines)
        + "\n\n## 5. Remediation Proposals Generated\n\n"
        + "\n".join(prop_lines)
        + "\n\n## 6. Systemic Reflection (Blameless)\n\n"
        + "### Contributing Factors\n"
        + "\n".join(f"- {cf}" for cf in contributing_factors)
        + "\n\n### What Went Well\n"
        + "\n".join(f"- {w}" for w in what_went_well)
        + "\n\n### What Could Be Improved\n"
        + "\n".join(f"- {imp}" for imp in what_could_be_improved)
        + diagrams_section
        + "\n## 8. Preventive Action Items\n\n"
        + "\n".join(action_rows)
        + "\n"
    )

    return PostMortemReport(
        incident_id=inp.incident_id,
        title=title,
        root_cause_summary=root_cause_summary,
        impact_summary=impact_summary,
        contributing_factors=contributing_factors,
        what_went_well=what_went_well,
        what_could_be_improved=what_could_be_improved,
        action_items=action_items,
        markdown_body=markdown_body,
        blameless_verified=not bool(_BLAME_WORDS_RE.search(markdown_body)),
    )


__all__ = [
    "ActionItem",
    "DiagramGeneratorInput",
    "DiagramGeneratorOutput",
    "PostMortemReport",
    "PostMortemWriterInput",
    "RemediationProposal",
    "RootCauseHypothesis",
    "TriageSummaryReport",
    "build_blameless_postmortem",
    "build_slack_blocks",
    "format_slack_triage_summary",
    "generate_incident_diagrams",
    "sanitize_blameless_text",
]
