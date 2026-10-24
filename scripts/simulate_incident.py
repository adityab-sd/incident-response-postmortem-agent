"""End-to-end CLI simulator for triggering all 5 incident scenarios."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from incident_agent.adapters.scenarios import SCENARIO_CATALOG
from incident_agent.config import get_config
from incident_agent.integrations.clients import PagerDutyClient, SlackClient
from incident_agent.persistence.store import SQLiteIncidentStore
from incident_agent.schemas import IncidentState, ScenarioType
from incident_agent.subagents.context import SubagentRuntimeContext
from incident_agent.supervisor.graph import IncidentSupervisor


def run_simulation_for_scenario(
    scenario: ScenarioType,
    *,
    approve_first_proposal: bool = True,
    db_path: str = ":memory:",
) -> IncidentState:
    """Run a complete incident lifecycle (trigger -> triage -> approve -> resolve -> post-mortem)."""
    cfg = get_config()
    store = SQLiteIncidentStore(db_path)
    ctx = SubagentRuntimeContext(config=cfg, store=store)
    slack = SlackClient(config=cfg)
    pd = PagerDutyClient(config=cfg)
    supervisor = IncidentSupervisor(
        ctx=ctx,
        slack_client=slack,
        pagerduty_client=pd,
    )

    spec = SCENARIO_CATALOG[scenario]
    incident_id = f"INC-SIM-{scenario.value.upper()[:10]}"
    base_now = datetime(2026, 10, 24, 14, 30, 0, tzinfo=UTC)

    raw_payload = {
        "incident": {
            "id": incident_id,
            "title": spec.alert_title,
            "priority": spec.severity.value,
            "service": {"name": spec.primary_service},
            "custom_details": {
                "scenario": scenario.value,
                "symptom": spec.symptom_summary,
                "environment": "production",
            },
        }
    }

    state = supervisor.start_incident_triage(
        incident_id=incident_id,
        raw_payload=raw_payload,
        source_type="pagerduty",
        created_at=base_now,
    )

    if approve_first_proposal and state.proposals:
        supervisor.approve_and_execute_proposal(
            incident_id=incident_id,
            proposal_id=state.proposals[0].proposal_id,
            approver_id="U01SRE_ONCALL",
            decision="approved",
            reason="Approved during simulated incident response",
        )

    final_state = supervisor.resolve_and_generate_postmortem(incident_id)
    return final_state


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate incident scenarios through the DevOps Triage Pod."
    )
    parser.add_argument(
        "--scenario",
        choices=["all"] + [s.value for s in ScenarioType],
        default="all",
        help="Scenario to run (default: all 5 scenarios).",
    )
    parser.add_argument(
        "--write-example-postmortem",
        type=str,
        default="",
        help="Optional path to write the generated post-mortem Markdown (e.g., docs/postmortem-example.md).",
    )
    args = parser.parse_args()

    target_scenarios = (
        list(ScenarioType) if args.scenario == "all" else [ScenarioType(args.scenario)]
    )

    for idx, scen in enumerate(target_scenarios, start=1):
        state = run_simulation_for_scenario(scen)
        top_hyp = state.hypotheses[0] if state.hypotheses else None
        top_prop = state.proposals[0] if state.proposals else None
        print("=" * 88)
        print(
            f"[{idx}/{len(target_scenarios)}] SCENARIO: {scen.value} | "
            f"INCIDENT: {state.incident_id} | SERVICE: {state.intake.service if state.intake else ''}"
        )
        print(f"  Status           : {state.status.value}")
        print(
            f"  Top Hypothesis   : {top_hyp.title if top_hyp else 'N/A'} "
            f"(cat={top_hyp.root_cause_category if top_hyp else ''}, conf={top_hyp.confidence if top_hyp else 0:.2f})"
        )
        print(f"  Cited Evidence   : {top_hyp.supporting_evidence_ids if top_hyp else []}")
        print(
            f"  Top Proposal     : [{top_prop.proposal_id if top_prop else ''}] "
            f"{top_prop.title if top_prop else ''} (proposal_only={top_prop.proposal_only if top_prop else True})"
        )
        print(f"  Timeline Events  : {len(state.timeline.events) if state.timeline else 0} events")
        print(
            f"  Tokens & Cost    : {state.total_tokens_used} tokens | ${state.total_cost_usd:.6f}"
        )

        if args.write_example_postmortem and idx == 1 and state.postmortem:
            out_path = Path(args.write_example_postmortem)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(state.postmortem.markdown_body, encoding="utf-8")
            print(f"  Wrote post-mortem example to: {out_path}")

    print("=" * 88)


if __name__ == "__main__":
    main()
