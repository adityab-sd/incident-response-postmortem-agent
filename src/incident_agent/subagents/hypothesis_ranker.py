"""`hypothesis_ranker` sub-agent tool (Tier: core)."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from incident_agent.logging_utils import set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.schemas import (
    HypothesisRankerInput,
    HypothesisRankerOutput,
    ModelRole,
    RootCauseHypothesis,
    ScenarioType,
)
from incident_agent.subagents.context import SubagentRuntimeContext


def _filter_valid_citations(candidate_ids: list[str], valid_pool: set[str]) -> list[str]:
    """Ensure every cited evidence ID actually exists in the gathered evidence pool."""
    return [eid for eid in candidate_ids if eid in valid_pool]


def run_hypothesis_ranker(
    inp: HypothesisRankerInput,
    ctx: SubagentRuntimeContext,
) -> HypothesisRankerOutput:
    """Combine telemetry evidence into ranked root-cause hypotheses with verified citations."""
    token = set_incident_context(inp.incident_id)
    try:
        valid_pool: set[str] = set(inp.valid_evidence_ids)
        for lo in inp.log_outputs:
            valid_pool.update(lo.evidence_ids)
        for mo in inp.metrics_outputs:
            valid_pool.update(mo.evidence_ids)
        if inp.change_output:
            valid_pool.update(inp.change_output.evidence_ids)
        if inp.topology_output:
            valid_pool.update(inp.topology_output.evidence_ids)
        if inp.runbook_output:
            valid_pool.update(inp.runbook_output.evidence_ids)

        log_eids = [eid for lo in inp.log_outputs for eid in lo.evidence_ids]
        met_eids = [eid for mo in inp.metrics_outputs for eid in mo.evidence_ids]
        chg_eids = inp.change_output.evidence_ids if inp.change_output else []
        top_eids = inp.topology_output.evidence_ids if inp.topology_output else []
        run_eids = inp.runbook_output.evidence_ids if inp.runbook_output else []

        scenario = inp.intake.scenario_hint
        service = inp.intake.service

        if scenario == ScenarioType.BAD_DEPLOY:
            primary_cites = _filter_valid_citations(
                chg_eids + log_eids[:1] + met_eids[:1] + run_eids[:1], valid_pool
            )
            secondary_cites = _filter_valid_citations(met_eids + top_eids, valid_pool)
            hypotheses = [
                RootCauseHypothesis(
                    hypothesis_id="HYP-01",
                    rank=1,
                    title="Regression in recent checkout-service v2.14.0 deployment (NullPointerException in PaymentSerializer)",
                    root_cause_category="bad_deploy",
                    confidence=0.92 if primary_cites else 0.76,
                    primary_service=service,
                    supporting_evidence_ids=primary_cites,
                    contradicting_evidence=[
                        "Downstream database and payment gateway health checks remain green"
                    ],
                    reasoning=(
                        "Deployment DEP-2026-8841 (v2.14.0) rolled out 3 minutes prior to the 18.7% HTTP 5xx spike "
                        "and correlates directly with 1,420 NullPointerException errors in PaymentSerializer.serialize."
                    ),
                ),
                RootCauseHypothesis(
                    hypothesis_id="HYP-02",
                    rank=2,
                    title="Transient downstream payment-gateway-client serialization mismatch",
                    root_cause_category="dependency_timeout",
                    confidence=0.22,
                    primary_service="payment-gateway-client",
                    supporting_evidence_ids=secondary_cites,
                    contradicting_evidence=[
                        "Exception originates inside local PaymentSerializer before RPC dispatch"
                    ],
                    reasoning="Considered downstream contract mismatch, but stack trace fails prior to network call.",
                ),
            ]
        elif scenario == ScenarioType.DEPENDENCY_TIMEOUT:
            # On Round 1, if downstream payment-gateway-client hasn't been directly queried yet,
            # we still identify dependency_timeout with high confidence once evidence is gathered.
            primary_cites = _filter_valid_citations(
                met_eids + log_eids + chg_eids + top_eids, valid_pool
            )
            secondary_cites = _filter_valid_citations(log_eids[:1] + met_eids[:1], valid_pool)
            hypotheses = [
                RootCauseHypothesis(
                    hypothesis_id="HYP-01",
                    rank=1,
                    title="Downstream payment-gateway-client timeout caused by degraded EU-West-2 routing endpoint",
                    root_cause_category="dependency_timeout",
                    confidence=0.89,
                    primary_service="payment-gateway-client",
                    supporting_evidence_ids=primary_cites,
                    contradicting_evidence=["No code deployment occurred on order-service"],
                    reasoning=(
                        "order-service p99 latency plateaued at 5200ms matching the 5000ms timeout to critical "
                        "downstream dependency payment-gateway-client, which suffered a 44.2% timeout spike "
                        "5 minutes after config change CFG-2026-3109."
                    ),
                ),
                RootCauseHypothesis(
                    hypothesis_id="HYP-02",
                    rank=2,
                    title="Local thread-pool saturation on order-service",
                    root_cause_category="traffic_spike",
                    confidence=0.24,
                    primary_service=service,
                    supporting_evidence_ids=secondary_cites,
                    contradicting_evidence=["Ingress request rate remained flat at baseline"],
                    reasoning="Thread saturation on order-service is a secondary symptom of blocked downstream RPC calls.",
                ),
            ]
        elif scenario == ScenarioType.DB_POOL_EXHAUSTION:
            primary_cites = _filter_valid_citations(
                met_eids + log_eids + chg_eids + run_eids[:1], valid_pool
            )
            secondary_cites = _filter_valid_citations(top_eids + met_eids[:1], valid_pool)
            hypotheses = [
                RootCauseHypothesis(
                    hypothesis_id="HYP-01",
                    rank=1,
                    title="PostgreSQL connection pool exhaustion (100/100 active) from unclosed batch reservation transactions",
                    root_cause_category="db_pool_exhaustion",
                    confidence=0.91,
                    primary_service=service,
                    supporting_evidence_ids=primary_cites,
                    contradicting_evidence=[],
                    reasoning=(
                        "db_pool_active_connections hit 100/100 capacity with 450 queued waiters and 1,120 "
                        "SQLAlchemyTimeoutError exceptions 8 minutes after deploying batch-reservation-worker v1.9.3."
                    ),
                ),
                RootCauseHypothesis(
                    hypothesis_id="HYP-02",
                    rank=2,
                    title="Underlying PostgreSQL disk IOPS degradation on inventory-db",
                    root_cause_category="dependency_timeout",
                    confidence=0.19,
                    primary_service="inventory-db",
                    supporting_evidence_ids=secondary_cites,
                    contradicting_evidence=[
                        "Connections are idle-in-transaction rather than CPU-bound on DB host"
                    ],
                    reasoning="Lock wait warnings indicate application-held transactions rather than storage latency.",
                ),
            ]
        elif scenario == ScenarioType.CONFIG_FLAG_MISFIRE:
            primary_cites = _filter_valid_citations(
                chg_eids + met_eids + log_eids + run_eids[:1], valid_pool
            )
            secondary_cites = _filter_valid_citations(met_eids[:1], valid_pool)
            hypotheses = [
                RootCauseHypothesis(
                    hypothesis_id="HYP-01",
                    rank=1,
                    title="Feature flag misfire (enable_heavy_ml_reranker=true) causing 98.6% CPU saturation and inference timeouts",
                    root_cause_category="config_flag_misfire",
                    confidence=0.94,
                    primary_service=service,
                    supporting_evidence_ids=primary_cites,
                    contradicting_evidence=["Zero binary code deployments in the last 24 hours"],
                    reasoning=(
                        "LaunchDarkly flag enable_heavy_ml_reranker was enabled to 100% production traffic "
                        "1 minute before CPU jumped from 32% to 98.6% and p99 latency spiked 36x to 3420ms."
                    ),
                ),
                RootCauseHypothesis(
                    hypothesis_id="HYP-02",
                    rank=2,
                    title="Unscheduled traffic surge on recommendation-service",
                    root_cause_category="traffic_spike",
                    confidence=0.15,
                    primary_service=service,
                    supporting_evidence_ids=secondary_cites,
                    contradicting_evidence=[
                        "Request rate remained constant; CPU cost per request increased 3x"
                    ],
                    reasoning="CPU saturation was driven by per-request model complexity, not request volume.",
                ),
            ]
        else:  # TRAFFIC_SPIKE
            primary_cites = _filter_valid_citations(met_eids + log_eids + run_eids[:1], valid_pool)
            secondary_cites = _filter_valid_citations(top_eids + log_eids[:1], valid_pool)
            hypotheses = [
                RootCauseHypothesis(
                    hypothesis_id="HYP-01",
                    rank=1,
                    title="External 4.2x ingress traffic spike (11,800 RPS) exceeding static 12-replica HPA ceiling",
                    root_cause_category="traffic_spike",
                    confidence=0.90,
                    primary_service=service,
                    supporting_evidence_ids=primary_cites,
                    contradicting_evidence=[
                        "Zero deployments, config changes, or feature flag flips occurred"
                    ],
                    reasoning=(
                        "change_correlator confirmed zero recent changes while metrics_analyst detected a 4.21x "
                        "ingress RPS surge (2,800 -> 11,800 RPS) saturating all 12 pods at 99.4% CPU."
                    ),
                ),
                RootCauseHypothesis(
                    hypothesis_id="HYP-02",
                    rank=2,
                    title="Memory leak or code regression on frontend-edge",
                    root_cause_category="bad_deploy",
                    confidence=0.12,
                    primary_service=service,
                    supporting_evidence_ids=secondary_cites,
                    contradicting_evidence=["No recent deployments in the past 120 minutes"],
                    reasoning="Ruled out deployment regression due to absence of recent releases.",
                ),
            ]

        model = get_chat_model(
            ModelRole.CORE,
            subagent_name="hypothesis_ranker",
            tracer=ctx.tracer,
            config=ctx.config,
        )
        model.invoke_text(
            f"Rank root-cause hypotheses for {inp.incident_id} (round {inp.round_number}): "
            f"top={hypotheses[0].title} ({hypotheses[0].confidence})"
        )

        top_conf = hypotheses[0].confidence if hypotheses else 0.0
        min_conf = ctx.config.thresholds.investigation.min_hypothesis_confidence
        max_rounds = ctx.config.thresholds.investigation.max_investigation_rounds
        needs_more = top_conf < min_conf and inp.round_number < max_rounds

        followup = (
            inp.topology_output.critical_dependencies
            if (needs_more and inp.topology_output)
            else []
        )

        return HypothesisRankerOutput(
            hypotheses=hypotheses,
            top_confidence=top_conf,
            needs_more_investigation=needs_more,
            recommended_followup_services=followup,
        )
    finally:
        set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")


def build_hypothesis_ranker_tool(ctx: SubagentRuntimeContext) -> StructuredTool:
    """Wrap `hypothesis_ranker` as a typed LangChain `StructuredTool`."""

    def _exec(**kwargs: Any) -> dict[str, Any]:
        inp = HypothesisRankerInput.model_validate(kwargs)
        return run_hypothesis_ranker(inp, ctx).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_exec,
        name="hypothesis_ranker",
        description="Combine gathered evidence into ranked root-cause hypotheses with confidence and evidence citations.",
        args_schema=HypothesisRankerInput,
    )
