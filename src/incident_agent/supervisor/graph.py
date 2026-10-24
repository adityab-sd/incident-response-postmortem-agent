"""LangGraph Supervisor state machine with parallel sub-agent execution, checkpointing, and resume."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from incident_agent.logging_utils import get_logger, log_event, set_incident_context
from incident_agent.models.factory import get_chat_model
from incident_agent.reporting.builders import format_slack_triage_summary
from incident_agent.safety.action_gate import ActionGate
from incident_agent.safety.budgets import BudgetEnforcer, BudgetExceededError
from incident_agent.schemas import (
    ActionExecutionRecord,
    ApprovalRecord,
    ChangeCorrelatorInput,
    DiagramGeneratorInput,
    HypothesisRankerInput,
    IncidentState,
    IncidentStatus,
    LogAnalystInput,
    MetricsAnalystInput,
    ModelRole,
    PostMortemWriterInput,
    RemediationProposerInput,
    RunbookRetrieverInput,
    TimelineBuilderInput,
    TopologyMapperInput,
    TriageIntakeInput,
    TriageSummaryReport,
)
from incident_agent.subagents import (
    SubagentRuntimeContext,
    build_all_subagent_tools,
    run_change_correlator,
    run_diagram_generator,
    run_hypothesis_ranker,
    run_log_analyst,
    run_metrics_analyst,
    run_postmortem_writer,
    run_remediation_proposer,
    run_runbook_retriever,
    run_timeline_builder,
    run_topology_mapper,
    run_triage_intake,
)

logger = get_logger("supervisor.graph")


class GraphStateDict(TypedDict):
    """TypedDict state passed through the LangGraph `StateGraph`."""

    incident_id: str
    state: IncidentState
    started_perf: float


class IncidentSupervisor:
    """Orchestrates the LangGraph triage pod, checkpointing, approval gate, and post-mortem flow."""

    def __init__(
        self,
        *,
        ctx: SubagentRuntimeContext,
        slack_client: Any = None,
        pagerduty_client: Any = None,
        budget_enforcer: BudgetEnforcer | None = None,
        stop_after_step: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.slack_client = slack_client
        self.pagerduty_client = pagerduty_client
        inv_cfg = ctx.config.thresholds.investigation
        self.budget_enforcer = budget_enforcer or BudgetEnforcer(
            max_tool_calls=inv_cfg.max_tool_calls_per_incident,
            max_tokens=inv_cfg.max_tokens_per_incident,
            max_rounds=inv_cfg.max_investigation_rounds,
        )
        self.stop_after_step = stop_after_step
        self.tools = build_all_subagent_tools(ctx)
        self._compiled_triage_graph = self._build_triage_graph()

    def _sync_tokens_and_cost(self, state: IncidentState) -> None:
        traces = self.ctx.tracer.get_traces(state.incident_id)
        state.total_tokens_used = sum(t.total_tokens for t in traces)
        state.total_cost_usd = round(sum(t.estimated_cost_usd for t in traces), 8)

    def _check_and_increment_budget(self, state: IncidentState, tool_name: str) -> None:
        self._sync_tokens_and_cost(state)
        self.budget_enforcer.check_can_proceed(
            incident_id=state.incident_id,
            current_tool_calls=state.tool_calls_count,
            current_tokens=state.total_tokens_used,
            current_round=max(1, state.investigation_round),
            next_tool_name=tool_name,
        )
        state.tool_calls_count += 1

    def _record_unavailable(self, state: IncidentState, source_name: str) -> None:
        if source_name not in state.unavailable_sources:
            state.unavailable_sources.append(source_name)

    def _node_intake(self, graph_state: GraphStateDict) -> GraphStateDict:
        state = graph_state["state"]
        if state.intake is not None and state.last_completed_step in {
            "intake",
            "investigate",
            "rank_hypotheses",
            "triaged",
        }:
            return graph_state

        try:
            self._check_and_increment_budget(state, "triage_intake")
            intake_out = run_triage_intake(
                TriageIntakeInput(
                    incident_id=state.incident_id,
                    source_type=state.source_type,
                    raw_payload=state.raw_trigger_payload,
                    received_at=state.created_at,
                ),
                self.ctx,
            )
            state.intake = intake_out
            state.status = IncidentStatus.INVESTIGATING
            state.updated_at = datetime.now(UTC)
            state.last_completed_step = "intake"
            self._sync_tokens_and_cost(state)
            self.ctx.store.save_checkpoint(
                state.incident_id, "intake", state.investigation_round, state
            )
        except BudgetExceededError:
            state.budget_halted = True
        return graph_state

    def _node_investigate(self, graph_state: GraphStateDict) -> GraphStateDict:
        state = graph_state["state"]
        if state.budget_halted or state.intake is None:
            return graph_state
        if self.stop_after_step == "intake":
            return graph_state

        # If resuming after `investigate` was already completed for round 1, skip to ranking
        if state.last_completed_step == "investigate" and state.investigation_round >= 1:
            return graph_state

        state.investigation_round += 1
        current_round = state.investigation_round
        intake = state.intake

        # Supervisor planning trace
        try:
            sup_model = get_chat_model(
                ModelRole.SUPERVISOR,
                subagent_name="supervisor_router",
                tracer=self.ctx.tracer,
                config=self.ctx.config,
            )
            sup_model.invoke_text(
                f"Supervisor planning investigation round {current_round} for "
                f"{state.incident_id} on service {intake.service}"
            )
        except Exception:
            pass

        target_service = intake.service
        if (
            current_round > 1
            and state.topology_output
            and state.topology_output.critical_dependencies
        ):
            target_service = state.topology_output.critical_dependencies[0]

        try:
            # 1. Topology Mapper (on Round 1)
            if state.topology_output is None:
                self._check_and_increment_budget(state, "topology_mapper")

            # Check budgets for parallel investigation sub-agents
            self._check_and_increment_budget(state, "log_analyst")
            self._check_and_increment_budget(state, "metrics_analyst")
            if state.change_output is None:
                self._check_and_increment_budget(state, "change_correlator")
            if state.runbook_output is None:
                self._check_and_increment_budget(state, "runbook_retriever")

            # Execute independent telemetry adapters in parallel
            with ThreadPoolExecutor(max_workers=5) as pool:
                topo_future = (
                    pool.submit(
                        run_topology_mapper,
                        TopologyMapperInput(
                            incident_id=state.incident_id,
                            service=intake.service,
                            scenario=intake.scenario_hint,
                        ),
                        self.ctx,
                    )
                    if state.topology_output is None
                    else None
                )
                log_future = pool.submit(
                    run_log_analyst,
                    LogAnalystInput(
                        incident_id=state.incident_id,
                        service=target_service,
                        window_start=intake.window_start,
                        window_end=intake.window_end,
                        scenario=intake.scenario_hint,
                    ),
                    self.ctx,
                )
                met_future = pool.submit(
                    run_metrics_analyst,
                    MetricsAnalystInput(
                        incident_id=state.incident_id,
                        service=target_service,
                        window_start=intake.window_start,
                        window_end=intake.window_end,
                        scenario=intake.scenario_hint,
                    ),
                    self.ctx,
                )
                chg_future = (
                    pool.submit(
                        run_change_correlator,
                        ChangeCorrelatorInput(
                            incident_id=state.incident_id,
                            services=[intake.service],
                            onset_time=intake.onset_time,
                            lookback_minutes=self.ctx.config.thresholds.investigation.change_lookback_minutes,
                            scenario=intake.scenario_hint,
                        ),
                        self.ctx,
                    )
                    if state.change_output is None
                    else None
                )
                run_future = (
                    pool.submit(
                        run_runbook_retriever,
                        RunbookRetrieverInput(
                            incident_id=state.incident_id,
                            service=intake.service,
                            symptoms=intake.symptom_tags,
                            scenario=intake.scenario_hint,
                        ),
                        self.ctx,
                    )
                    if state.runbook_output is None
                    else None
                )

                if topo_future is not None:
                    topo_res = topo_future.result()
                    state.topology_output = topo_res
                    state.evidence_ids.extend(topo_res.evidence_ids)
                    if not topo_res.source_available:
                        self._record_unavailable(state, "topology")

                log_res = log_future.result()
                state.log_outputs.append(log_res)
                state.evidence_ids.extend(log_res.evidence_ids)
                if not log_res.source_available:
                    self._record_unavailable(state, "logs")

                met_res = met_future.result()
                state.metrics_outputs.append(met_res)
                state.evidence_ids.extend(met_res.evidence_ids)
                if not met_res.source_available:
                    self._record_unavailable(state, "metrics")

                if chg_future is not None:
                    chg_res = chg_future.result()
                    state.change_output = chg_res
                    state.evidence_ids.extend(chg_res.evidence_ids)
                    if not chg_res.source_available:
                        self._record_unavailable(state, "deploys")

                if run_future is not None:
                    run_res = run_future.result()
                    state.runbook_output = run_res
                    state.evidence_ids.extend(run_res.evidence_ids)
                    if not run_res.source_available:
                        self._record_unavailable(state, "runbooks")

            state.updated_at = datetime.now(UTC)
            state.last_completed_step = "investigate"
            self._sync_tokens_and_cost(state)
            self.ctx.store.save_checkpoint(
                state.incident_id, "investigate", state.investigation_round, state
            )
        except BudgetExceededError:
            state.budget_halted = True

        return graph_state

    def _node_rank_hypotheses(self, graph_state: GraphStateDict) -> GraphStateDict:
        state = graph_state["state"]
        if state.intake is None or self.stop_after_step in {"intake", "investigate"}:
            return graph_state
        if state.budget_halted and state.hypotheses:
            return graph_state

        try:
            self._check_and_increment_budget(state, "hypothesis_ranker")
            rank_out = run_hypothesis_ranker(
                HypothesisRankerInput(
                    incident_id=state.incident_id,
                    intake=state.intake,
                    log_outputs=state.log_outputs,
                    metrics_outputs=state.metrics_outputs,
                    change_output=state.change_output,
                    topology_output=state.topology_output,
                    runbook_output=state.runbook_output,
                    valid_evidence_ids=list(dict.fromkeys(state.evidence_ids)),
                    round_number=max(1, state.investigation_round),
                ),
                self.ctx,
            )
            state.hypotheses = rank_out.hypotheses
            state.updated_at = datetime.now(UTC)
            state.last_completed_step = "rank_hypotheses"
            self._sync_tokens_and_cost(state)
            self.ctx.store.save_checkpoint(
                state.incident_id, "rank_hypotheses", state.investigation_round, state
            )
        except BudgetExceededError:
            state.budget_halted = True
        return graph_state

    def _route_after_ranking(self, graph_state: GraphStateDict) -> str:
        state = graph_state["state"]
        if self.stop_after_step in {"intake", "investigate", "rank_hypotheses"}:
            return "propose_remediation"
        if state.budget_halted:
            return "propose_remediation"

        top_conf = state.hypotheses[0].confidence if state.hypotheses else 0.0
        min_conf = self.ctx.config.thresholds.investigation.min_hypothesis_confidence
        max_rounds = self.ctx.config.thresholds.investigation.max_investigation_rounds

        if (
            top_conf < min_conf
            and state.investigation_round < max_rounds
            and state.tool_calls_count < self.budget_enforcer.max_tool_calls
        ):
            # Mark step so _node_investigate runs the next round
            state.last_completed_step = "needs_followup_round"
            return "investigate"
        return "propose_remediation"

    def _node_propose_remediation(self, graph_state: GraphStateDict) -> GraphStateDict:
        state = graph_state["state"]
        if state.intake is None or self.stop_after_step in {"intake", "investigate"}:
            return graph_state

        if not state.proposals:
            try:
                if not state.budget_halted:
                    self._check_and_increment_budget(state, "remediation_proposer")
                prop_out = run_remediation_proposer(
                    RemediationProposerInput(
                        incident_id=state.incident_id,
                        service=state.intake.service,
                        hypotheses=state.hypotheses,
                        changes=state.change_output.changes if state.change_output else [],
                        runbooks=state.runbook_output.runbooks if state.runbook_output else [],
                    ),
                    self.ctx,
                )
                state.proposals = prop_out.proposals
            except BudgetExceededError:
                state.budget_halted = True

        self._sync_tokens_and_cost(state)
        duration = round(time.perf_counter() - graph_state["started_perf"], 3)
        state.triaged_at = datetime.now(UTC)
        state.status = IncidentStatus.TRIAGED_AWAITING_APPROVAL

        summary = TriageSummaryReport(
            incident_id=state.incident_id,
            service=state.intake.service,
            severity=state.intake.severity,
            status=state.status,
            symptom_summary=state.intake.symptom_summary,
            investigation_rounds=state.investigation_round,
            tool_calls_used=state.tool_calls_count,
            total_tokens_used=state.total_tokens_used,
            total_cost_usd=state.total_cost_usd,
            triage_duration_seconds=duration,
            budget_halted=state.budget_halted,
            unavailable_sources=list(state.unavailable_sources),
            hypotheses=state.hypotheses,
            proposals=state.proposals,
        )
        summary.slack_markdown = format_slack_triage_summary(summary)
        state.triage_summary = summary
        state.last_completed_step = "triaged"
        state.updated_at = datetime.now(UTC)
        self.ctx.store.save_checkpoint(
            state.incident_id, "triaged", state.investigation_round, state
        )

        if self.slack_client is not None:
            self.slack_client.post_triage_summary(
                summary,
                channel=state.slack_channel,
                thread_ts=state.slack_thread_ts,
            )
        if self.pagerduty_client is not None:
            self.pagerduty_client.add_incident_note(
                state.incident_id,
                state.intake.external_alert_id,
                f"Automated triage complete. Top hypothesis: {state.hypotheses[0].title if state.hypotheses else 'Pending'}",
            )

        return graph_state

    def _build_triage_graph(self) -> Any:
        builder: Any = StateGraph(GraphStateDict)
        builder.add_node("intake", self._node_intake)
        builder.add_node("investigate", self._node_investigate)
        builder.add_node("rank_hypotheses", self._node_rank_hypotheses)
        builder.add_node("propose_remediation", self._node_propose_remediation)

        builder.add_edge(START, "intake")
        builder.add_edge("intake", "investigate")
        builder.add_edge("investigate", "rank_hypotheses")
        builder.add_conditional_edges(
            "rank_hypotheses",
            self._route_after_ranking,
            {
                "investigate": "investigate",
                "propose_remediation": "propose_remediation",
            },
        )
        builder.add_edge("propose_remediation", END)
        return builder.compile()

    def start_incident_triage(
        self,
        *,
        incident_id: str,
        raw_payload: dict[str, Any],
        source_type: str = "pagerduty",
        slack_channel: str | None = None,
        created_at: datetime | None = None,
    ) -> IncidentState:
        """Create a new incident record, post Slack ack, and run the LangGraph triage workflow."""
        token = set_incident_context(incident_id)
        try:
            now = created_at or datetime.now(UTC)
            channel = slack_channel or self.ctx.config.env.slack_default_channel
            state = IncidentState(
                incident_id=incident_id,
                status=IncidentStatus.TRIGGERED,
                created_at=now,
                updated_at=now,
                acknowledged_at=now,
                slack_channel=channel,
                raw_trigger_payload=raw_payload,
                source_type=source_type,
            )
            if self.slack_client is not None:
                thread_ts = self.slack_client.post_acknowledgment(
                    incident_id=incident_id,
                    service=str(raw_payload.get("service", "production-service")),
                    severity=str(raw_payload.get("severity", "SEV1")),
                    channel=channel,
                )
                state.slack_thread_ts = thread_ts

            self.ctx.store.save_state(state)
            result = self._compiled_triage_graph.invoke(
                {
                    "incident_id": incident_id,
                    "state": state,
                    "started_perf": time.perf_counter(),
                }
            )
            final_state: IncidentState = result["state"]
            return final_state
        finally:
            set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")

    def resume_incident(self, incident_id: str) -> IncidentState:
        """Resume an interrupted investigation from its latest persisted checkpoint."""
        checkpoint = self.ctx.store.get_latest_checkpoint(incident_id)
        if checkpoint is None:
            stored = self.ctx.store.get_state(incident_id)
            if stored is None:
                raise KeyError(f"No incident or checkpoint found for {incident_id}")
            state = stored
        else:
            step_name, round_num, state = checkpoint
            log_event(
                logger,
                logging.INFO,
                "Resuming incident investigation from persisted checkpoint",
                incident_id=incident_id,
                checkpoint_step=step_name,
                round_number=round_num,
            )

        self.stop_after_step = None
        result = self._compiled_triage_graph.invoke(
            {
                "incident_id": incident_id,
                "state": state,
                "started_perf": time.perf_counter(),
            }
        )
        final_state: IncidentState = result["state"]
        return final_state

    def approve_and_execute_proposal(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        approver_id: str,
        decision: str = "approved",
        reason: str = "Approved by on-call engineer",
    ) -> tuple[ApprovalRecord, ActionExecutionRecord | None]:
        """Record human decision and execute the proposal if approved."""
        state = self.ctx.store.get_state(incident_id)
        if state is None:
            raise KeyError(f"Incident {incident_id} not found")

        gate = ActionGate(self.ctx.store)
        approval = gate.record_approval(
            incident_id=incident_id,
            proposal_id=proposal_id,
            approver_id=approver_id,
            decision=decision,
            reason=reason,
        )
        state.approvals.append(approval)

        execution: ActionExecutionRecord | None = None
        if approval.decision == "approved":
            proposal = next((p for p in state.proposals if p.proposal_id == proposal_id), None)
            if proposal is None:
                raise KeyError(f"Proposal {proposal_id} not found on incident {incident_id}")
            execution = gate.execute_approved_action(incident_id=incident_id, proposal=proposal)
            state.executions.append(execution)
            state.status = IncidentStatus.MITIGATING

        state.updated_at = datetime.now(UTC)
        self.ctx.store.save_state(state)
        return approval, execution

    def resolve_and_generate_postmortem(
        self,
        incident_id: str,
        *,
        resolved_at: datetime | None = None,
    ) -> IncidentState:
        """Run the post-incident resolution pipeline: timeline_builder, diagram_generator, postmortem_writer."""
        token = set_incident_context(incident_id)
        try:
            state = self.ctx.store.get_state(incident_id)
            if state is None:
                raise KeyError(f"Incident {incident_id} not found")
            if state.intake is None:
                state = self.resume_incident(incident_id)
            assert state.intake is not None

            state.resolved_at = resolved_at or datetime.now(UTC)
            state.approvals = self.ctx.store.list_approvals(incident_id)
            state.executions = self.ctx.store.list_executions(incident_id)

            # 1. Timeline Builder
            state.tool_calls_count += 1
            timeline = run_timeline_builder(
                TimelineBuilderInput(
                    incident_id=incident_id,
                    intake=state.intake,
                    log_outputs=state.log_outputs,
                    metrics_outputs=state.metrics_outputs,
                    change_output=state.change_output,
                    approvals=state.approvals,
                    executions=state.executions,
                    acknowledged_at=state.acknowledged_at,
                    triaged_at=state.triaged_at,
                    resolved_at=state.resolved_at,
                ),
                self.ctx,
            )
            state.timeline = timeline

            # 2. Diagram Generator
            state.tool_calls_count += 1
            diagrams = run_diagram_generator(
                DiagramGeneratorInput(
                    incident_id=incident_id,
                    intake=state.intake,
                    topology_output=state.topology_output,
                    timeline=timeline,
                    top_hypothesis=state.hypotheses[0] if state.hypotheses else None,
                ),
                self.ctx,
            )
            state.diagrams = diagrams

            # 3. Post-Mortem Writer
            state.tool_calls_count += 1
            postmortem = run_postmortem_writer(
                PostMortemWriterInput(
                    incident_id=incident_id,
                    intake=state.intake,
                    timeline=timeline,
                    hypotheses=state.hypotheses,
                    proposals=state.proposals,
                    diagrams=diagrams,
                    unavailable_sources=state.unavailable_sources,
                ),
                self.ctx,
            )
            state.postmortem = postmortem
            state.status = IncidentStatus.RESOLVED_POSTMORTEM_COMPLETE
            state.last_completed_step = "resolved"
            state.updated_at = datetime.now(UTC)
            self._sync_tokens_and_cost(state)
            self.ctx.store.save_checkpoint(
                incident_id, "resolved", state.investigation_round, state
            )

            if self.slack_client is not None:
                self.slack_client.post_postmortem(
                    incident_id=incident_id,
                    markdown_body=postmortem.markdown_body,
                    channel=state.slack_channel,
                    thread_ts=state.slack_thread_ts,
                )

            return state
        finally:
            set_incident_context(token.old_value if isinstance(token.old_value, str) else "SYSTEM")
