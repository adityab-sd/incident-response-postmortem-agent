"""Pydantic v2 domain models and typed tool input/output schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    """Incident severity tiers."""

    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class IncidentStatus(StrEnum):
    """Lifecycle states of an incident."""

    TRIGGERED = "TRIGGERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    TRIAGED_AWAITING_APPROVAL = "TRIAGED_AWAITING_APPROVAL"
    MITIGATING = "MITIGATING"
    RESOLVED_POSTMORTEM_COMPLETE = "RESOLVED_POSTMORTEM_COMPLETE"


class ScenarioType(StrEnum):
    """Built-in synthetic incident scenarios."""

    BAD_DEPLOY = "bad_deploy"
    DEPENDENCY_TIMEOUT = "dependency_timeout"
    DB_POOL_EXHAUSTION = "db_pool_exhaustion"
    CONFIG_FLAG_MISFIRE = "config_flag_misfire"
    TRAFFIC_SPIKE = "traffic_spike"


class ActionRiskLevel(StrEnum):
    """Risk classification for remediation proposals."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionType(StrEnum):
    """Supported remediation action types."""

    ROLLBACK_DEPLOY = "rollback_deploy"
    TOGGLE_FEATURE_FLAG = "toggle_feature_flag"
    SCALE_REPLICAS = "scale_replicas"
    RESTART_PODS = "restart_pods"
    INCREASE_POOL_SIZE = "increase_pool_size"
    RATE_LIMIT_TRAFFIC = "rate_limit_traffic"


class ModelRole(StrEnum):
    """Tiered LLM roles."""

    SUPERVISOR = "supervisor"
    CORE = "core"
    LONG_CONTEXT = "long_context"


# ---------------------------------------------------------------------------
# Telemetry & Adapter Data Models
# ---------------------------------------------------------------------------


class LogEntry(BaseModel):
    """Single structured log line returned by the LogAdapter."""

    model_config = ConfigDict(extra="forbid")

    log_id: str
    timestamp: datetime
    service: str
    level: str
    message: str
    trace_id: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class LogCluster(BaseModel):
    """Clustered error signature across log entries."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    service: str
    signature: str
    error_code: str
    count: int
    first_seen: datetime
    last_seen: datetime
    sample_message: str


class MetricPoint(BaseModel):
    """Single timestamped metric sample."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    value: float


class MetricSeries(BaseModel):
    """Time series for a specific metric on a service."""

    model_config = ConfigDict(extra="forbid")

    service: str
    metric_name: str
    unit: str
    points: list[MetricPoint] = Field(default_factory=list)


class MetricAnomaly(BaseModel):
    """Detected anomaly on a service metric."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    service: str
    metric_name: str
    baseline_value: float
    peak_value: float
    deviation_factor: float
    onset_time: datetime
    unit: str
    description: str


class ChangeEvent(BaseModel):
    """Deployment, configuration change, or feature flag update."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    change_id: str
    service: str
    change_type: str  # deployment | config_change | feature_flag | infra_scaling
    version_or_key: str
    author_role: str
    timestamp: datetime
    summary: str
    rollback_command: str


class ServiceDependency(BaseModel):
    """Dependency link between two services."""

    model_config = ConfigDict(extra="forbid")

    service_name: str
    dependency_type: str  # internal_service | database | cache | external_api
    protocol: str  # grpc | http | postgres | redis
    criticality: str  # critical | degraded_ok


class ServiceTopology(BaseModel):
    """Service dependency graph for a given microservice."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    service: str
    tier: int
    owner_team: str
    sla_availability: str
    upstream_callers: list[str] = Field(default_factory=list)
    downstream_dependencies: list[ServiceDependency] = Field(default_factory=list)


class RunbookDocument(BaseModel):
    """Operational runbook entry."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    runbook_id: str
    title: str
    service: str
    symptom_tags: list[str] = Field(default_factory=list)
    diagnostic_steps: list[str] = Field(default_factory=list)
    safe_remediation_steps: list[str] = Field(default_factory=list)


class PastIncidentSummary(BaseModel):
    """Historical post-mortem summary for similar past incidents."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    past_incident_id: str
    title: str
    service: str
    root_cause_category: str
    summary: str
    resolution_applied: str


class EvidenceRecord(BaseModel):
    """Unified evidence record persisted during investigation."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    incident_id: str
    source_type: str  # logs | metrics | deploys | topology | runbooks
    service: str
    timestamp: datetime
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sub-Agent Tool Input & Output Schemas (All 11 Tools)
# ---------------------------------------------------------------------------


class TriageIntakeInput(BaseModel):
    """Input schema for the `triage_intake` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    source_type: str  # pagerduty | slack | alertmanager
    raw_payload: dict[str, Any]
    received_at: datetime


class TriageIntakeOutput(BaseModel):
    """Output schema for the `triage_intake` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    external_alert_id: str
    service: str
    severity: Severity
    environment: str
    title: str
    symptom_summary: str
    symptom_tags: list[str] = Field(default_factory=list)
    scenario_hint: ScenarioType
    onset_time: datetime
    window_start: datetime
    window_end: datetime


class LogAnalystInput(BaseModel):
    """Input schema for the `log_analyst` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    service: str
    window_start: datetime
    window_end: datetime
    severity_filter: str = "ERROR"
    scenario: ScenarioType = ScenarioType.BAD_DEPLOY


class LogAnalystOutput(BaseModel):
    """Output schema for the `log_analyst` tool."""

    model_config = ConfigDict(extra="forbid")

    service: str
    clusters: list[LogCluster] = Field(default_factory=list)
    first_error_at: datetime | None = None
    total_error_count: int = 0
    evidence_ids: list[str] = Field(default_factory=list)
    routed_tier: ModelRole = ModelRole.CORE
    estimated_input_tokens: int = 0
    injections_neutralized: int = 0
    source_available: bool = True
    error_message: str | None = None


class MetricsAnalystInput(BaseModel):
    """Input schema for the `metrics_analyst` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    service: str
    window_start: datetime
    window_end: datetime
    scenario: ScenarioType = ScenarioType.BAD_DEPLOY


class MetricsAnalystOutput(BaseModel):
    """Output schema for the `metrics_analyst` tool."""

    model_config = ConfigDict(extra="forbid")

    service: str
    anomalies: list[MetricAnomaly] = Field(default_factory=list)
    earliest_onset: datetime | None = None
    peak_error_rate: float = 0.0
    peak_latency_p99_ms: float = 0.0
    saturation_summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    source_available: bool = True
    error_message: str | None = None


class ChangeCorrelatorInput(BaseModel):
    """Input schema for the `change_correlator` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    services: list[str]
    onset_time: datetime
    lookback_minutes: int = 120
    scenario: ScenarioType = ScenarioType.BAD_DEPLOY


class ChangeCorrelatorOutput(BaseModel):
    """Output schema for the `change_correlator` tool."""

    model_config = ConfigDict(extra="forbid")

    changes: list[ChangeEvent] = Field(default_factory=list)
    most_suspect_change_id: str | None = None
    most_suspect_evidence_id: str | None = None
    correlation_score: float = 0.0
    correlation_reasoning: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    source_available: bool = True
    error_message: str | None = None


class TopologyMapperInput(BaseModel):
    """Input schema for the `topology_mapper` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    service: str
    scenario: ScenarioType = ScenarioType.BAD_DEPLOY


class TopologyMapperOutput(BaseModel):
    """Output schema for the `topology_mapper` tool."""

    model_config = ConfigDict(extra="forbid")

    topology: ServiceTopology | None = None
    upstream_callers: list[str] = Field(default_factory=list)
    downstream_services: list[str] = Field(default_factory=list)
    critical_dependencies: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_available: bool = True
    error_message: str | None = None


class RunbookRetrieverInput(BaseModel):
    """Input schema for the `runbook_retriever` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    service: str
    symptoms: list[str]
    scenario: ScenarioType = ScenarioType.BAD_DEPLOY


class RunbookRetrieverOutput(BaseModel):
    """Output schema for the `runbook_retriever` tool."""

    model_config = ConfigDict(extra="forbid")

    runbooks: list[RunbookDocument] = Field(default_factory=list)
    past_postmortems: list[PastIncidentSummary] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_available: bool = True
    error_message: str | None = None


class RootCauseHypothesis(BaseModel):
    """Ranked root-cause hypothesis with confidence and evidence citations."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    rank: int
    title: str
    root_cause_category: str  # bad_deploy | dependency_timeout | db_pool_exhaustion | config_flag_misfire | traffic_spike
    confidence: float = Field(ge=0.0, le=1.0)
    primary_service: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    reasoning: str


class HypothesisRankerInput(BaseModel):
    """Input schema for the `hypothesis_ranker` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    intake: TriageIntakeOutput
    log_outputs: list[LogAnalystOutput] = Field(default_factory=list)
    metrics_outputs: list[MetricsAnalystOutput] = Field(default_factory=list)
    change_output: ChangeCorrelatorOutput | None = None
    topology_output: TopologyMapperOutput | None = None
    runbook_output: RunbookRetrieverOutput | None = None
    valid_evidence_ids: list[str] = Field(default_factory=list)
    round_number: int = 1


class HypothesisRankerOutput(BaseModel):
    """Output schema for the `hypothesis_ranker` tool."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    top_confidence: float = 0.0
    needs_more_investigation: bool = False
    recommended_followup_services: list[str] = Field(default_factory=list)


class RemediationProposal(BaseModel):
    """Proposed reversible remediation action — strictly proposal only."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    action_type: ActionType
    title: str
    target_service: str
    command_or_api_call: str
    risk_level: ActionRiskLevel
    estimated_recovery_minutes: int
    rollback_path: str
    rationale: str
    supporting_hypothesis_id: str
    proposal_only: bool = True
    requires_human_approval: bool = True


class RemediationProposerInput(BaseModel):
    """Input schema for the `remediation_proposer` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    service: str
    hypotheses: list[RootCauseHypothesis]
    changes: list[ChangeEvent] = Field(default_factory=list)
    runbooks: list[RunbookDocument] = Field(default_factory=list)


class RemediationProposerOutput(BaseModel):
    """Output schema for the `remediation_proposer` tool."""

    model_config = ConfigDict(extra="forbid")

    proposals: list[RemediationProposal] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    """Single chronological event in the incident timeline."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    phase: str  # pre_incident | onset | detection | triage | mitigation | resolution
    source: str
    evidence_id: str | None = None
    description: str
    is_key_event: bool = False


class TimelineBuilderInput(BaseModel):
    """Input schema for the `timeline_builder` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    intake: TriageIntakeOutput
    log_outputs: list[LogAnalystOutput] = Field(default_factory=list)
    metrics_outputs: list[MetricsAnalystOutput] = Field(default_factory=list)
    change_output: ChangeCorrelatorOutput | None = None
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    executions: list[ActionExecutionRecord] = Field(default_factory=list)
    acknowledged_at: datetime | None = None
    triaged_at: datetime | None = None
    resolved_at: datetime | None = None


class TimelineBuilderOutput(BaseModel):
    """Output schema for the `timeline_builder` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    events: list[TimelineEvent] = Field(default_factory=list)
    ttd_seconds: int = 0
    tta_seconds: int = 0
    ttm_seconds: int = 0
    ttr_seconds: int = 0


class ActionItem(BaseModel):
    """Follow-up preventive or corrective action item in a post-mortem."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    category: str  # detection | prevention | mitigation | process
    priority: str  # P0 | P1 | P2
    owner_role: str
    description: str


class DiagramGeneratorInput(BaseModel):
    """Input schema for the `diagram_generator` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    intake: TriageIntakeOutput
    topology_output: TopologyMapperOutput | None = None
    timeline: TimelineBuilderOutput
    top_hypothesis: RootCauseHypothesis | None = None


class DiagramGeneratorOutput(BaseModel):
    """Output schema for the `diagram_generator` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_sequence_mermaid: str
    blast_radius_mermaid: str


class PostMortemWriterInput(BaseModel):
    """Input schema for the `postmortem_writer` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    intake: TriageIntakeOutput
    timeline: TimelineBuilderOutput
    hypotheses: list[RootCauseHypothesis]
    proposals: list[RemediationProposal]
    diagrams: DiagramGeneratorOutput | None = None
    unavailable_sources: list[str] = Field(default_factory=list)


class PostMortemReport(BaseModel):
    """Output schema for the `postmortem_writer` tool."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    title: str
    root_cause_summary: str
    impact_summary: str
    contributing_factors: list[str] = Field(default_factory=list)
    what_went_well: list[str] = Field(default_factory=list)
    what_could_be_improved: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    markdown_body: str
    blameless_verified: bool = True


# ---------------------------------------------------------------------------
# Governance, Tracing & Persistence State Models
# ---------------------------------------------------------------------------


class ApprovalRecord(BaseModel):
    """Human approval or rejection record for a remediation proposal."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    incident_id: str
    proposal_id: str
    approver_id: str
    decision: str  # approved | rejected
    reason: str = ""
    approved_at: datetime


class ActionExecutionRecord(BaseModel):
    """Audit record of an approved remediation execution."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str
    incident_id: str
    proposal_id: str
    approver_id: str
    action_type: ActionType
    target_service: str
    command_executed: str
    status: str  # executed | failed
    output: str
    executed_at: datetime


class LLMTrace(BaseModel):
    """Telemetry record for a single LLM invocation."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    incident_id: str
    subagent_name: str
    role: ModelRole
    provider: str
    model_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    estimated_cost_usd: float
    fallback_used: bool = False
    fallback_reason: str | None = None
    timestamp: datetime


class TriageSummaryReport(BaseModel):
    """Complete structured triage summary posted to Slack."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    service: str
    severity: Severity
    status: IncidentStatus
    symptom_summary: str
    investigation_rounds: int
    tool_calls_used: int
    total_tokens_used: int
    total_cost_usd: float
    triage_duration_seconds: float
    budget_halted: bool = False
    unavailable_sources: list[str] = Field(default_factory=list)
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    proposals: list[RemediationProposal] = Field(default_factory=list)
    slack_markdown: str = ""


class IncidentState(BaseModel):
    """Full serializable state of an incident across its lifecycle."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    status: IncidentStatus = IncidentStatus.TRIGGERED
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None = None
    triaged_at: datetime | None = None
    resolved_at: datetime | None = None
    slack_channel: str = "C01INCIDENTS"
    slack_thread_ts: str | None = None
    raw_trigger_payload: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "pagerduty"
    intake: TriageIntakeOutput | None = None
    log_outputs: list[LogAnalystOutput] = Field(default_factory=list)
    metrics_outputs: list[MetricsAnalystOutput] = Field(default_factory=list)
    change_output: ChangeCorrelatorOutput | None = None
    topology_output: TopologyMapperOutput | None = None
    runbook_output: RunbookRetrieverOutput | None = None
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    proposals: list[RemediationProposal] = Field(default_factory=list)
    timeline: TimelineBuilderOutput | None = None
    diagrams: DiagramGeneratorOutput | None = None
    postmortem: PostMortemReport | None = None
    triage_summary: TriageSummaryReport | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    unavailable_sources: list[str] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    executions: list[ActionExecutionRecord] = Field(default_factory=list)
    investigation_round: int = 0
    tool_calls_count: int = 0
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    budget_halted: bool = False
    last_completed_step: str = "init"
