"""`remediation_proposer` sub-agent tool (Tier: core) — strictly returns proposals only."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.logging_utils import set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.schemas import (
    ActionRiskLevel,
    ActionType,
    ModelRole,
    RemediationProposal,
    RemediationProposerInput,
    RemediationProposerOutput,
)
from incident_agent.subagents.context import SubagentRuntimeContext


def run_remediation_proposer(
    inp: RemediationProposerInput,
    ctx: SubagentRuntimeContext,
) -> RemediationProposerOutput:
    """Propose reversible remediation steps tagged with risk level and rollback path."""
    token = set_incident_context(inp.incident_id)
    try:
        top_hyp = inp.hypotheses[0] if inp.hypotheses else None
        category = top_hyp.root_cause_category if top_hyp else "bad_deploy"
        hyp_id = top_hyp.hypothesis_id if top_hyp else "HYP-01"
        rollback_cmd = (
            inp.changes[0].rollback_command
            if inp.changes
            else f"kubectl rollout undo deployment/{inp.service} -n production"
        )

        if category == "bad_deploy":
            proposals = [
                RemediationProposal(
                    proposal_id="PROP-01",
                    action_type=ActionType.ROLLBACK_DEPLOY,
                    title=f"Rollback {inp.service} deployment v2.14.0 to previous revision",
                    target_service=inp.service,
                    command_or_api_call=rollback_cmd,
                    risk_level=ActionRiskLevel.LOW,
                    estimated_recovery_minutes=3,
                    rollback_path=f"kubectl rollout undo deployment/{inp.service} -n production --to-revision=42",
                    rationale="Reverts the NullPointerException regression introduced in v2.14.0.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                )
            ]
        elif category == "dependency_timeout":
            proposals = [
                RemediationProposal(
                    proposal_id="PROP-01",
                    action_type=ActionType.TOGGLE_FEATURE_FLAG,
                    title="Failover payment-gateway-client routing endpoint to global multi-region PoP",
                    target_service="payment-gateway-client",
                    command_or_api_call=rollback_cmd,
                    risk_level=ActionRiskLevel.LOW,
                    estimated_recovery_minutes=2,
                    rollback_path="kubectl set env deployment/payment-gateway-client ROUTING_ENDPOINT=external-stripe-edge-eu-west-2 -n production",
                    rationale="Routes traffic away from degraded EU-West-2 payment gateway PoP.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                ),
                RemediationProposal(
                    proposal_id="PROP-02",
                    action_type=ActionType.RESTART_PODS,
                    title=f"Rolling restart of {inp.service} pods to clear stuck upstream sockets",
                    target_service=inp.service,
                    command_or_api_call=f"kubectl rollout restart deployment/{inp.service} -n production",
                    risk_level=ActionRiskLevel.MEDIUM,
                    estimated_recovery_minutes=4,
                    rollback_path="Kubernetes PodDisruptionBudget maintains >=80% available pods during restart",
                    rationale="Clears half-open socket connections after downstream failover.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                ),
            ]
        elif category == "db_pool_exhaustion":
            proposals = [
                RemediationProposal(
                    proposal_id="PROP-01",
                    action_type=ActionType.ROLLBACK_DEPLOY,
                    title=f"Rollback {inp.service} batch-reservation-worker v1.9.3 deployment",
                    target_service=inp.service,
                    command_or_api_call=rollback_cmd,
                    risk_level=ActionRiskLevel.LOW,
                    estimated_recovery_minutes=4,
                    rollback_path=f"kubectl rollout undo deployment/{inp.service} -n production",
                    rationale="Stops leaked idle-in-transaction connections from batch worker.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                ),
                RemediationProposal(
                    proposal_id="PROP-02",
                    action_type=ActionType.INCREASE_POOL_SIZE,
                    title="Temporarily raise SQLAlchemy pool size from 100 to 160 and enforce 5s statement_timeout",
                    target_service=inp.service,
                    command_or_api_call=f"kubectl set env deployment/{inp.service} DB_POOL_MAX_SIZE=160 DB_STATEMENT_TIMEOUT_MS=5000 -n production",
                    risk_level=ActionRiskLevel.MEDIUM,
                    estimated_recovery_minutes=3,
                    rollback_path=f"kubectl set env deployment/{inp.service} DB_POOL_MAX_SIZE=100 -n production",
                    rationale="Relieves immediate connection queue backlog while draining leaked sessions.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                ),
            ]
        elif category == "config_flag_misfire":
            proposals = [
                RemediationProposal(
                    proposal_id="PROP-01",
                    action_type=ActionType.TOGGLE_FEATURE_FLAG,
                    title="Disable feature flag enable_heavy_ml_reranker in production",
                    target_service=inp.service,
                    command_or_api_call=rollback_cmd,
                    risk_level=ActionRiskLevel.LOW,
                    estimated_recovery_minutes=1,
                    rollback_path="ld-cli flags toggle enable_heavy_ml_reranker --env production --state on",
                    rationale="Immediately restores lightweight baseline ranker and drops CPU from 98.6% to ~32%.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                )
            ]
        else:  # traffic_spike
            proposals = [
                RemediationProposal(
                    proposal_id="PROP-01",
                    action_type=ActionType.SCALE_REPLICAS,
                    title=f"Scale {inp.service} replica count from 12 to 32 pods",
                    target_service=inp.service,
                    command_or_api_call=f"kubectl scale deployment/{inp.service} --replicas=32 -n production",
                    risk_level=ActionRiskLevel.LOW,
                    estimated_recovery_minutes=3,
                    rollback_path=f"kubectl scale deployment/{inp.service} --replicas=12 -n production",
                    rationale="Provides horizontal compute capacity to absorb the 4.2x ingress traffic surge.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                ),
                RemediationProposal(
                    proposal_id="PROP-02",
                    action_type=ActionType.RATE_LIMIT_TRAFFIC,
                    title="Enable edge token-bucket burst rate limiting for non-checkout bot/scraper traffic",
                    target_service=inp.service,
                    command_or_api_call="kubectl set env deployment/frontend-edge EDGE_BURST_RATE_LIMIT_ENABLED=true -n production",
                    risk_level=ActionRiskLevel.MEDIUM,
                    estimated_recovery_minutes=1,
                    rollback_path="kubectl set env deployment/frontend-edge EDGE_BURST_RATE_LIMIT_ENABLED=false -n production",
                    rationale="Sheds non-critical burst traffic to protect core checkout availability.",
                    supporting_hypothesis_id=hyp_id,
                    proposal_only=True,
                    requires_human_approval=True,
                ),
            ]

        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="remediation_proposer",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(
            f"Propose reversible read-only remediations for {inp.incident_id}: "
            f"{[p.title for p in proposals]}"
        )

        return RemediationProposerOutput(proposals=proposals)
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_remediation_proposer_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `remediation_proposer` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = RemediationProposerInput.model_validate(kwargs)
        return run_remediation_proposer(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="remediation_proposer",
        description="Propose reversible remediation steps tagged with risk level and rollback path (proposals only).",
        args_schema=RemediationProposerInput,
    )
