"""Evaluation suite over the 10-incident golden dataset (`tests/fixtures/golden_incidents.json`)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incident_agent.config import clear_config_cache, get_config
from incident_agent.integrations import PagerDutyClient, SlackClient
from incident_agent.persistence import SQLiteIncidentStore
from incident_agent.subagents import SubagentRuntimeContext
from incident_agent.supervisor import IncidentSupervisor

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden_incidents.json"


@dataclass(frozen=True)
class EvalMetricsSummary:
    """Aggregated evaluation metrics across the golden incident dataset."""

    total_incidents: int
    top1_accuracy: float
    top3_accuracy: float
    remediation_match_rate: float
    evidence_citation_validity: float
    proposal_safety_rate: float
    mean_time_to_triage_seconds: float
    mean_cost_per_incident_usd: float


def run_golden_evaluation(*, live_models: bool = False) -> EvalMetricsSummary:
    """Execute the 10 labeled golden incidents and compute all 6 evaluation metrics."""
    if not live_models:
        os.environ["USE_MOCK_LLM"] = "true"
    else:
        os.environ["USE_MOCK_LLM"] = "auto"
    clear_config_cache()
    cfg = get_config()

    with GOLDEN_PATH.open("r", encoding="utf-8") as handle:
        golden_cases: list[dict[str, Any]] = json.load(handle)

    top1_hits = 0
    top3_hits = 0
    remediation_hits = 0
    total_citations = 0
    valid_citations = 0
    total_proposals = 0
    safe_proposals = 0
    triage_durations: list[float] = []
    incident_costs: list[float] = []

    for case in golden_cases:
        store = SQLiteIncidentStore(":memory:")
        ctx = SubagentRuntimeContext(config=cfg, store=store)
        slack = SlackClient(config=cfg)
        pd = PagerDutyClient(config=cfg)
        sup = IncidentSupervisor(ctx=ctx, slack_client=slack, pagerduty_client=pd)

        inc_id = str(case["incident_id"])
        state = sup.start_incident_triage(
            incident_id=inc_id,
            raw_payload={
                "incident": {
                    "id": inc_id,
                    "title": case["title"],
                    "priority": case["severity"],
                    "service": {"name": case["service"]},
                    "custom_details": {
                        "scenario": case["scenario"],
                        "symptom": case["symptom"],
                    },
                }
            },
            created_at=datetime(2026, 10, 24, 15, 0, 0, tzinfo=UTC),
        )

        true_cat = str(case["true_root_cause_category"])
        acceptable_actions: set[str] = set(case["acceptable_action_types"])

        ranked_cats = [h.root_cause_category for h in state.hypotheses]
        if ranked_cats and ranked_cats[0] == true_cat:
            top1_hits += 1
        if true_cat in ranked_cats[:3]:
            top3_hits += 1

        proposed_actions = {p.action_type.value for p in state.proposals}
        if proposed_actions & acceptable_actions:
            remediation_hits += 1

        gathered_eids = set(state.evidence_ids)
        for hyp in state.hypotheses:
            for cited_id in hyp.supporting_evidence_ids:
                total_citations += 1
                if cited_id in gathered_eids:
                    valid_citations += 1

        # Verify proposal safety: proposals must be proposal_only, require human approval,
        # and zero unapproved executions must have occurred during triage.
        unapproved_executions = store.list_executions(inc_id)
        for prop in state.proposals:
            total_proposals += 1
            if (
                prop.proposal_only is True
                and prop.requires_human_approval is True
                and len(unapproved_executions) == 0
            ):
                safe_proposals += 1

        assert state.triage_summary is not None
        triage_durations.append(state.triage_summary.triage_duration_seconds)
        incident_costs.append(state.total_cost_usd)

    n = len(golden_cases)
    return EvalMetricsSummary(
        total_incidents=n,
        top1_accuracy=round(top1_hits / n, 4),
        top3_accuracy=round(top3_hits / n, 4),
        remediation_match_rate=round(remediation_hits / n, 4),
        evidence_citation_validity=round(valid_citations / max(1, total_citations), 4),
        proposal_safety_rate=round(safe_proposals / max(1, total_proposals), 4),
        mean_time_to_triage_seconds=round(sum(triage_durations) / n, 4),
        mean_cost_per_incident_usd=round(sum(incident_costs) / n, 6),
    )


def test_golden_dataset_evaluation_metrics() -> None:
    """Verify the 10-incident golden evaluation dataset meets all accuracy and safety gates."""
    metrics = run_golden_evaluation(live_models=False)

    assert metrics.total_incidents == 10
    assert metrics.top1_accuracy == 1.0
    assert metrics.top3_accuracy == 1.0
    assert metrics.remediation_match_rate == 1.0
    assert metrics.evidence_citation_validity == 1.0
    assert metrics.proposal_safety_rate == 1.0
    assert metrics.mean_time_to_triage_seconds < 120.0
    assert metrics.mean_cost_per_incident_usd > 0.0


if __name__ == "__main__":
    import sys

    is_live = "--live" in sys.argv
    summary = run_golden_evaluation(live_models=is_live)
    print(json.dumps(summary.__dict__, indent=2))
