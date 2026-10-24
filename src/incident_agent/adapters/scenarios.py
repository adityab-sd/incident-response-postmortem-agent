"""Realistic 5-scenario synthetic data library for end-to-end offline testing and evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from incident_agent.schemas import (
    ChangeEvent,
    LogCluster,
    LogEntry,
    MetricAnomaly,
    PastIncidentSummary,
    RunbookDocument,
    ScenarioType,
    ServiceDependency,
    ServiceTopology,
    Severity,
)


@dataclass(frozen=True)
class ScenarioDefinition:
    """Complete synthetic telemetry bundle for an incident scenario."""

    scenario: ScenarioType
    primary_service: str
    root_cause_service: str
    severity: Severity
    alert_title: str
    symptom_summary: str
    symptom_tags: list[str]
    true_root_cause_category: str
    acceptable_action_types: list[str]


SCENARIO_CATALOG: dict[ScenarioType, ScenarioDefinition] = {
    ScenarioType.BAD_DEPLOY: ScenarioDefinition(
        scenario=ScenarioType.BAD_DEPLOY,
        primary_service="checkout-service",
        root_cause_service="checkout-service",
        severity=Severity.SEV1,
        alert_title="High 5xx Error Rate on checkout-service (>18.5%)",
        symptom_summary="HTTP 5xx error rate spiked from 0.2% to 18.7% on checkout-service with NullPointerException in PaymentSerializer",
        symptom_tags=["5xx_spike", "null_pointer", "checkout", "deployment_regression"],
        true_root_cause_category="bad_deploy",
        acceptable_action_types=["rollback_deploy"],
    ),
    ScenarioType.DEPENDENCY_TIMEOUT: ScenarioDefinition(
        scenario=ScenarioType.DEPENDENCY_TIMEOUT,
        primary_service="order-service",
        root_cause_service="payment-gateway-client",
        severity=Severity.SEV1,
        alert_title="Downstream Timeout & p99 Latency Breach on order-service",
        symptom_summary="order-service p99 latency surged to 5200ms due to upstream timeouts calling downstream payment-gateway-client",
        symptom_tags=["dependency_timeout", "latency_spike", "upstream_timeout", "circuit_breaker"],
        true_root_cause_category="dependency_timeout",
        acceptable_action_types=["toggle_feature_flag", "restart_pods", "rate_limit_traffic"],
    ),
    ScenarioType.DB_POOL_EXHAUSTION: ScenarioDefinition(
        scenario=ScenarioType.DB_POOL_EXHAUSTION,
        primary_service="inventory-service",
        root_cause_service="inventory-service",
        severity=Severity.SEV2,
        alert_title="Database Connection Pool Exhaustion on inventory-service",
        symptom_summary="inventory-service active DB connections saturated at 100/100 with 450 queued waiters and SQLAlchemy TimeoutError",
        symptom_tags=["db_pool_exhaustion", "connection_timeout", "database_saturation"],
        true_root_cause_category="db_pool_exhaustion",
        acceptable_action_types=["increase_pool_size", "restart_pods", "rollback_deploy"],
    ),
    ScenarioType.CONFIG_FLAG_MISFIRE: ScenarioDefinition(
        scenario=ScenarioType.CONFIG_FLAG_MISFIRE,
        primary_service="recommendation-service",
        root_cause_service="recommendation-service",
        severity=Severity.SEV2,
        alert_title="CPU Saturation & OOMKills on recommendation-service",
        symptom_summary="recommendation-service CPU hit 98% and p99 latency hit 3400ms immediately after enabling feature flag enable_heavy_ml_reranker",
        symptom_tags=["feature_flag", "cpu_saturation", "latency_spike", "config_flag_misfire"],
        true_root_cause_category="config_flag_misfire",
        acceptable_action_types=["toggle_feature_flag"],
    ),
    ScenarioType.TRAFFIC_SPIKE: ScenarioDefinition(
        scenario=ScenarioType.TRAFFIC_SPIKE,
        primary_service="frontend-edge",
        root_cause_service="frontend-edge",
        severity=Severity.SEV2,
        alert_title="4x Traffic Surge & HTTP 429/503 Saturation on frontend-edge",
        symptom_summary="Ingress request rate surged 4.2x (from 2,800 RPS to 11,800 RPS) causing pod CPU exhaustion and HTTP 503 responses with zero recent deploys",
        symptom_tags=["traffic_spike", "high_rps", "cpu_saturation", "autoscaling"],
        true_root_cause_category="traffic_spike",
        acceptable_action_types=["scale_replicas", "rate_limit_traffic"],
    ),
}


def infer_scenario_from_service_or_text(service: str, text: str) -> ScenarioType:
    """Deterministically infer the scenario from service name or alert description."""
    combined = f"{service} {text}".lower()
    if (
        "dependency_timeout" in combined
        or "payment-gateway" in combined
        or "order-service" in combined
    ):
        return ScenarioType.DEPENDENCY_TIMEOUT
    if "db_pool" in combined or "connection pool" in combined or "inventory-service" in combined:
        return ScenarioType.DB_POOL_EXHAUSTION
    if "flag" in combined or "reranker" in combined or "recommendation-service" in combined:
        return ScenarioType.CONFIG_FLAG_MISFIRE
    if "traffic_spike" in combined or "surge" in combined or "frontend-edge" in combined:
        return ScenarioType.TRAFFIC_SPIKE
    return ScenarioType.BAD_DEPLOY


def build_scenario_logs(
    scenario: ScenarioType,
    service: str,
    base_time: datetime,
    *,
    large_bundle: bool = False,
) -> tuple[list[LogEntry], list[LogCluster]]:
    """Generate realistic log entries and error clusters for a scenario."""
    onset = base_time - timedelta(minutes=12)

    if scenario == ScenarioType.BAD_DEPLOY:
        clusters = [
            LogCluster(
                evidence_id="EV-LOG-BD01",
                service=service,
                signature="NullPointerException: Cannot read field 'currency_iso' on null PaymentContext in PaymentSerializer.serialize (v2.14.0)",
                error_code="HTTP_500_NPE",
                count=1420,
                first_seen=onset,
                last_seen=base_time,
                sample_message=(
                    "ERROR [checkout-service] NullPointerException in PaymentSerializer.serialize "
                    "for user customer_jane@example.com from 10.42.8.19 with token Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.sig123456"
                ),
            ),
            LogCluster(
                evidence_id="EV-LOG-BD02",
                service=service,
                signature="CheckoutTransactionAborted: Upstream handler returned 500 Internal Server Error",
                error_code="CHECKOUT_ABORT_500",
                count=1385,
                first_seen=onset + timedelta(seconds=15),
                last_seen=base_time,
                sample_message="ERROR [checkout-service] CheckoutTransactionAborted on /api/v2/checkout/submit",
            ),
        ]
    elif scenario == ScenarioType.DEPENDENCY_TIMEOUT:
        clusters = [
            LogCluster(
                evidence_id="EV-LOG-DT01",
                service=service,
                signature="UpstreamTimeoutError: Deadline exceeded (5000ms) awaiting response from payment-gateway-client:8443/authorize",
                error_code="UPSTREAM_TIMEOUT_504",
                count=890,
                first_seen=onset,
                last_seen=base_time,
                sample_message=(
                    "ERROR [order-service] UpstreamTimeoutError calling payment-gateway-client "
                    "(peer=172.16.40.12:8443, api_key=sk-live9876543210abcdef)"
                ),
            ),
            LogCluster(
                evidence_id="EV-LOG-DT02",
                service="payment-gateway-client",
                signature="CircuitBreakerOpenException: Downstream TLS handshake stalled against external-stripe-edge",
                error_code="CIRCUIT_BREAKER_OPEN",
                count=640,
                first_seen=onset - timedelta(seconds=20),
                last_seen=base_time,
                sample_message="ERROR [payment-gateway-client] CircuitBreakerOpenException after 50 consecutive timeouts",
            ),
        ]
    elif scenario == ScenarioType.DB_POOL_EXHAUSTION:
        clusters = [
            LogCluster(
                evidence_id="EV-LOG-DB01",
                service=service,
                signature="SQLAlchemyTimeoutError: QueuePool limit of size 100 overflow 0 reached, connection timed out after 30.00s",
                error_code="DB_POOL_EXHAUSTED",
                count=1120,
                first_seen=onset,
                last_seen=base_time,
                sample_message=(
                    "ERROR [inventory-service] QueuePool limit of size 100 reached on "
                    "postgresql://inv_user:SuperSecretDbPass99@inventory-db.internal:5432/inventory"
                ),
            ),
            LogCluster(
                evidence_id="EV-LOG-DB02",
                service=service,
                signature="UnclosedTransactionWarning: Lock wait timeout exceeded on table inventory_reservations",
                error_code="DB_LOCK_WAIT",
                count=310,
                first_seen=onset - timedelta(minutes=2),
                last_seen=base_time,
                sample_message="WARN [inventory-service] UnclosedTransactionWarning holding connection > 120s",
            ),
        ]
    elif scenario == ScenarioType.CONFIG_FLAG_MISFIRE:
        clusters = [
            LogCluster(
                evidence_id="EV-LOG-CF01",
                service=service,
                signature="InferenceDeadlineExceeded: HeavyMLReranker (flag=enable_heavy_ml_reranker) exceeded 3000ms CPU budget",
                error_code="RERANKER_CPU_TIMEOUT",
                count=2150,
                first_seen=onset,
                last_seen=base_time,
                sample_message=(
                    "ERROR [recommendation-service] InferenceDeadlineExceeded in HeavyMLReranker "
                    "(flag enable_heavy_ml_reranker=true, worker_ip=192.168.14.88)"
                ),
            ),
            LogCluster(
                evidence_id="EV-LOG-CF02",
                service=service,
                signature="MemoryPressureWarning: Tensor allocation in HeavyMLReranker reached 94% container RSS limit",
                error_code="OOM_RISK_WARN",
                count=540,
                first_seen=onset + timedelta(minutes=1),
                last_seen=base_time,
                sample_message="WARN [recommendation-service] MemoryPressureWarning on pod recommendation-7d4b9",
            ),
        ]
    else:  # TRAFFIC_SPIKE
        clusters = [
            LogCluster(
                evidence_id="EV-LOG-TS01",
                service=service,
                signature="RateLimitAndQueueFullError: Worker accept queue saturated (4096/4096) under 11,800 RPS flash-sale surge",
                error_code="HTTP_503_QUEUE_FULL",
                count=4820,
                first_seen=onset,
                last_seen=base_time,
                sample_message=(
                    "ERROR [frontend-edge] RateLimitAndQueueFullError dropping request from "
                    "client_ip=203.0.113.77 user=shopper99@example.org"
                ),
            ),
            LogCluster(
                evidence_id="EV-LOG-TS02",
                service=service,
                signature="UpstreamBackpressureWarning: HPA at current maxReplicas=12; CPU throttled at 99.4%",
                error_code="HPA_MAX_REPLICAS_HIT",
                count=920,
                first_seen=onset + timedelta(minutes=2),
                last_seen=base_time,
                sample_message="WARN [frontend-edge] HPA ceiling reached during unscheduled traffic surge",
            ),
        ]

    entries: list[LogEntry] = []
    repeat_count = 400 if large_bundle else 6
    for idx in range(repeat_count):
        cluster = clusters[idx % len(clusters)]
        padding = (" [verbose_stack_frame=" + ("x" * 280) + "]") if large_bundle else ""
        entries.append(
            LogEntry(
                log_id=f"LOG-{scenario.value[:2].upper()}-{idx + 1:04d}",
                timestamp=onset + timedelta(seconds=idx * 5),
                service=cluster.service,
                level="ERROR",
                message=f"{cluster.sample_message} | sig={cluster.signature}{padding}",
                trace_id=f"tr-{scenario.value[:4]}-{idx + 1000}",
                attributes={"error_code": cluster.error_code, "scenario": scenario.value},
            )
        )
    return entries, clusters


def build_scenario_metrics(
    scenario: ScenarioType,
    service: str,
    base_time: datetime,
) -> list[MetricAnomaly]:
    """Generate realistic metric anomalies for a scenario."""
    onset = base_time - timedelta(minutes=12)

    if scenario == ScenarioType.BAD_DEPLOY:
        return [
            MetricAnomaly(
                evidence_id="EV-MET-BD01",
                service=service,
                metric_name="http_error_rate_5xx_pct",
                baseline_value=0.2,
                peak_value=18.7,
                deviation_factor=93.5,
                onset_time=onset,
                unit="percent",
                description="HTTP 5xx error rate jumped from 0.2% baseline to 18.7% immediately after v2.14.0 rollout",
            ),
            MetricAnomaly(
                evidence_id="EV-MET-BD02",
                service=service,
                metric_name="http_latency_p99_ms",
                baseline_value=140.0,
                peak_value=890.0,
                deviation_factor=6.36,
                onset_time=onset,
                unit="ms",
                description="p99 latency increased 6.36x due to exception stack unwinding on failed checkouts",
            ),
        ]
    if scenario == ScenarioType.DEPENDENCY_TIMEOUT:
        return [
            MetricAnomaly(
                evidence_id="EV-MET-DT01",
                service=service,
                metric_name="http_latency_p99_ms",
                baseline_value=180.0,
                peak_value=5200.0,
                deviation_factor=28.89,
                onset_time=onset,
                unit="ms",
                description="order-service p99 latency plateaued at 5200ms (matching the 5000ms downstream client timeout)",
            ),
            MetricAnomaly(
                evidence_id="EV-MET-DT02",
                service="payment-gateway-client",
                metric_name="upstream_timeout_rate_pct",
                baseline_value=0.05,
                peak_value=44.2,
                deviation_factor=884.0,
                onset_time=onset - timedelta(seconds=20),
                unit="percent",
                description="Downstream payment-gateway-client timeout rate surged to 44.2%",
            ),
        ]
    if scenario == ScenarioType.DB_POOL_EXHAUSTION:
        return [
            MetricAnomaly(
                evidence_id="EV-MET-DB01",
                service=service,
                metric_name="db_pool_active_connections",
                baseline_value=24.0,
                peak_value=100.0,
                deviation_factor=4.17,
                onset_time=onset,
                unit="connections",
                description="Database connection pool saturated at 100/100 max connections (100% utilization)",
            ),
            MetricAnomaly(
                evidence_id="EV-MET-DB02",
                service=service,
                metric_name="db_pool_wait_queue_length",
                baseline_value=0.0,
                peak_value=450.0,
                deviation_factor=450.0,
                onset_time=onset + timedelta(seconds=30),
                unit="threads",
                description="450 worker threads blocked awaiting an available PostgreSQL connection",
            ),
        ]
    if scenario == ScenarioType.CONFIG_FLAG_MISFIRE:
        return [
            MetricAnomaly(
                evidence_id="EV-MET-CF01",
                service=service,
                metric_name="cpu_utilization_pct",
                baseline_value=32.0,
                peak_value=98.6,
                deviation_factor=3.08,
                onset_time=onset,
                unit="percent",
                description="Container CPU utilization jumped from 32% to 98.6% upon enabling enable_heavy_ml_reranker",
            ),
            MetricAnomaly(
                evidence_id="EV-MET-CF02",
                service=service,
                metric_name="http_latency_p99_ms",
                baseline_value=95.0,
                peak_value=3420.0,
                deviation_factor=36.0,
                onset_time=onset,
                unit="ms",
                description="Recommendation inference p99 latency spiked 36x to 3420ms",
            ),
        ]
    # TRAFFIC_SPIKE
    return [
        MetricAnomaly(
            evidence_id="EV-MET-TS01",
            service=service,
            metric_name="http_requests_per_second",
            baseline_value=2800.0,
            peak_value=11800.0,
            deviation_factor=4.21,
            onset_time=onset,
            unit="rps",
            description="Ingress request rate surged 4.21x from 2,800 RPS baseline to 11,800 RPS",
        ),
        MetricAnomaly(
            evidence_id="EV-MET-TS02",
            service=service,
            metric_name="cpu_utilization_pct",
            baseline_value=41.0,
            peak_value=99.4,
            deviation_factor=2.42,
            onset_time=onset + timedelta(minutes=1),
            unit="percent",
            description="All 12 frontend-edge pods saturated at 99.4% CPU with 503 queue drops",
        ),
    ]


def build_scenario_changes(
    scenario: ScenarioType,
    base_time: datetime,
) -> list[ChangeEvent]:
    """Generate recent deployment, config, or feature-flag changes for a scenario."""
    onset = base_time - timedelta(minutes=12)

    if scenario == ScenarioType.BAD_DEPLOY:
        return [
            ChangeEvent(
                evidence_id="EV-CHG-BD01",
                change_id="DEP-2026-8841",
                service="checkout-service",
                change_type="deployment",
                version_or_key="v2.14.0 (commit 9f4b2c1)",
                author_role="Release Pipeline / Checkout Engineering",
                timestamp=onset - timedelta(minutes=3),
                summary="Deployed checkout-service v2.14.0 refactoring PaymentSerializer currency handling (3 minutes prior to 5xx onset)",
                rollback_command="kubectl rollout undo deployment/checkout-service -n production --to-revision=41",
            )
        ]
    if scenario == ScenarioType.DEPENDENCY_TIMEOUT:
        return [
            ChangeEvent(
                evidence_id="EV-CHG-DT01",
                change_id="CFG-2026-3109",
                service="payment-gateway-client",
                change_type="config_change",
                version_or_key="ROUTING_ENDPOINT=external-stripe-edge-eu-west-2",
                author_role="Payments Platform Team",
                timestamp=onset - timedelta(minutes=5),
                summary="Switched payment-gateway-client primary routing endpoint to degraded EU-West-2 PoP 5 minutes before timeout spike",
                rollback_command="kubectl set env deployment/payment-gateway-client ROUTING_ENDPOINT=external-stripe-edge-global -n production",
            )
        ]
    if scenario == ScenarioType.DB_POOL_EXHAUSTION:
        return [
            ChangeEvent(
                evidence_id="EV-CHG-DB01",
                change_id="DEP-2026-7712",
                service="inventory-service",
                change_type="deployment",
                version_or_key="v1.9.3 (batch-reservation-worker)",
                author_role="Inventory Platform Team",
                timestamp=onset - timedelta(minutes=8),
                summary="Deployed inventory-service v1.9.3 introducing long-running batch reservation transactions without connection release",
                rollback_command="kubectl rollout undo deployment/inventory-service -n production",
            )
        ]
    if scenario == ScenarioType.CONFIG_FLAG_MISFIRE:
        return [
            ChangeEvent(
                evidence_id="EV-CHG-CF01",
                change_id="FLAG-2026-5504",
                service="recommendation-service",
                change_type="feature_flag",
                version_or_key="enable_heavy_ml_reranker=true (100% traffic)",
                author_role="Search & Personalization Team",
                timestamp=onset - timedelta(minutes=1),
                summary="Enabled LaunchDarkly feature flag 'enable_heavy_ml_reranker' to 100% production traffic 1 minute before CPU saturation",
                rollback_command="ld-cli flags toggle enable_heavy_ml_reranker --env production --state off",
            )
        ]
    # TRAFFIC_SPIKE has ZERO recent deploys or flag flips near onset (key differentiator!)
    return []


def build_scenario_topology(
    scenario: ScenarioType,
    service: str,
) -> ServiceTopology:
    """Return service dependency topology for the target service."""
    topologies: dict[ScenarioType, ServiceTopology] = {
        ScenarioType.BAD_DEPLOY: ServiceTopology(
            evidence_id="EV-TOP-BD01",
            service=service,
            tier=1,
            owner_team="Checkout Core Pod",
            sla_availability="99.99%",
            upstream_callers=["frontend-edge", "mobile-bff"],
            downstream_dependencies=[
                ServiceDependency(
                    service_name="payment-gateway-client",
                    dependency_type="internal_service",
                    protocol="grpc",
                    criticality="critical",
                ),
                ServiceDependency(
                    service_name="checkout-postgres",
                    dependency_type="database",
                    protocol="postgres",
                    criticality="critical",
                ),
                ServiceDependency(
                    service_name="cart-redis",
                    dependency_type="cache",
                    protocol="redis",
                    criticality="degraded_ok",
                ),
            ],
        ),
        ScenarioType.DEPENDENCY_TIMEOUT: ServiceTopology(
            evidence_id="EV-TOP-DT01",
            service=service,
            tier=1,
            owner_team="Order Fulfillment Pod",
            sla_availability="99.95%",
            upstream_callers=["checkout-service", "api-gateway"],
            downstream_dependencies=[
                ServiceDependency(
                    service_name="payment-gateway-client",
                    dependency_type="internal_service",
                    protocol="http",
                    criticality="critical",
                ),
                ServiceDependency(
                    service_name="orders-postgres",
                    dependency_type="database",
                    protocol="postgres",
                    criticality="critical",
                ),
            ],
        ),
        ScenarioType.DB_POOL_EXHAUSTION: ServiceTopology(
            evidence_id="EV-TOP-DB01",
            service=service,
            tier=1,
            owner_team="Inventory & Catalog Pod",
            sla_availability="99.95%",
            upstream_callers=["checkout-service", "order-service"],
            downstream_dependencies=[
                ServiceDependency(
                    service_name="inventory-db",
                    dependency_type="database",
                    protocol="postgres",
                    criticality="critical",
                ),
                ServiceDependency(
                    service_name="inventory-redis-cache",
                    dependency_type="cache",
                    protocol="redis",
                    criticality="degraded_ok",
                ),
            ],
        ),
        ScenarioType.CONFIG_FLAG_MISFIRE: ServiceTopology(
            evidence_id="EV-TOP-CF01",
            service=service,
            tier=2,
            owner_team="Personalization & ML Pod",
            sla_availability="99.90%",
            upstream_callers=["frontend-edge", "product-detail-service"],
            downstream_dependencies=[
                ServiceDependency(
                    service_name="feature-flag-service",
                    dependency_type="internal_service",
                    protocol="http",
                    criticality="critical",
                ),
                ServiceDependency(
                    service_name="vector-feature-store",
                    dependency_type="database",
                    protocol="grpc",
                    criticality="critical",
                ),
            ],
        ),
        ScenarioType.TRAFFIC_SPIKE: ServiceTopology(
            evidence_id="EV-TOP-TS01",
            service=service,
            tier=1,
            owner_team="Edge & Ingress Platform Pod",
            sla_availability="99.99%",
            upstream_callers=["cloudflare-cdn", "public-load-balancer"],
            downstream_dependencies=[
                ServiceDependency(
                    service_name="checkout-service",
                    dependency_type="internal_service",
                    protocol="http",
                    criticality="critical",
                ),
                ServiceDependency(
                    service_name="recommendation-service",
                    dependency_type="internal_service",
                    protocol="http",
                    criticality="degraded_ok",
                ),
            ],
        ),
    }
    return topologies[scenario]


def build_scenario_runbooks(
    scenario: ScenarioType,
    service: str,
) -> tuple[list[RunbookDocument], list[PastIncidentSummary]]:
    """Return matching operational runbooks and past incident summaries."""
    if scenario == ScenarioType.BAD_DEPLOY:
        return (
            [
                RunbookDocument(
                    evidence_id="EV-RUN-BD01",
                    runbook_id="RB-CHK-001",
                    title="Checkout Service 5xx Error Spike & Deployment Rollback Runbook",
                    service=service,
                    symptom_tags=["5xx_spike", "null_pointer", "deployment_regression"],
                    diagnostic_steps=[
                        "Check recent ArgoCD/Kubernetes deployments within 60 minutes of 5xx onset.",
                        "Inspect error log clusters for newly introduced exception signatures.",
                        "Verify database and downstream payment health to rule out cascading failures.",
                    ],
                    safe_remediation_steps=[
                        "Propose immediate rollback of checkout-service deployment to last known good revision.",
                        "Monitor 5xx rate recovery below 0.5% for 10 minutes post-rollback.",
                    ],
                )
            ],
            [
                PastIncidentSummary(
                    evidence_id="EV-PM-BD01",
                    past_incident_id="INC-20260412-009",
                    title="Checkout 5xx Outage due to Unhandled Null Currency Field in v2.8.0",
                    service=service,
                    root_cause_category="bad_deploy",
                    summary="A schema serializer change caused NullPointerExceptions on guest checkouts.",
                    resolution_applied="Rolled back deployment checkout-service within 8 minutes.",
                )
            ],
        )

    if scenario == ScenarioType.DEPENDENCY_TIMEOUT:
        return (
            [
                RunbookDocument(
                    evidence_id="EV-RUN-DT01",
                    runbook_id="RB-ORD-004",
                    title="Downstream Payment Gateway Latency & Circuit Breaker Runbook",
                    service=service,
                    symptom_tags=["dependency_timeout", "upstream_timeout", "latency_spike"],
                    diagnostic_steps=[
                        "Check topology map for critical downstream dependencies of order-service.",
                        "Compare order-service p99 latency against payment-gateway-client timeout rate.",
                        "Verify if payment-gateway-client routing endpoint config was recently updated.",
                    ],
                    safe_remediation_steps=[
                        "Failover payment-gateway-client routing endpoint to healthy global PoP.",
                        "Enable fallback async payment queue flag if downstream degradation persists.",
                    ],
                )
            ],
            [
                PastIncidentSummary(
                    evidence_id="EV-PM-DT01",
                    past_incident_id="INC-20260619-014",
                    title="Order Service Cascading Timeouts from Regional Payment Gateway PoP Degradation",
                    service=service,
                    root_cause_category="dependency_timeout",
                    summary="Regional payment PoP packet loss caused 5000ms timeouts on order-service.",
                    resolution_applied="Switched ROUTING_ENDPOINT to global multi-region endpoint.",
                )
            ],
        )

    if scenario == ScenarioType.DB_POOL_EXHAUSTION:
        return (
            [
                RunbookDocument(
                    evidence_id="EV-RUN-DB01",
                    runbook_id="RB-INV-002",
                    title="PostgreSQL Connection Pool Saturation & Lock Contention Runbook",
                    service=service,
                    symptom_tags=[
                        "db_pool_exhaustion",
                        "connection_timeout",
                        "database_saturation",
                    ],
                    diagnostic_steps=[
                        "Inspect db_pool_active_connections and db_pool_wait_queue_length metrics.",
                        "Check for recent deployments introducing unclosed transactions or batch workers.",
                    ],
                    safe_remediation_steps=[
                        "Rollback batch worker deployment or temporarily increase connection pool limit.",
                        "Perform rolling restart of inventory-service pods to clear leaked connections.",
                    ],
                )
            ],
            [
                PastIncidentSummary(
                    evidence_id="EV-PM-DB01",
                    past_incident_id="INC-20260503-003",
                    title="Inventory DB QueuePool Exhaustion from Long-Running Reconciliation Transaction",
                    service=service,
                    root_cause_category="db_pool_exhaustion",
                    summary="Idle-in-transaction connections exhausted the 100-connection pool limit.",
                    resolution_applied="Rolled back worker deploy and added 5s statement_timeout.",
                )
            ],
        )

    if scenario == ScenarioType.CONFIG_FLAG_MISFIRE:
        return (
            [
                RunbookDocument(
                    evidence_id="EV-RUN-CF01",
                    runbook_id="RB-REC-007",
                    title="Feature Flag Misfire & ML Inference CPU Saturation Runbook",
                    service=service,
                    symptom_tags=["feature_flag", "cpu_saturation", "config_flag_misfire"],
                    diagnostic_steps=[
                        "Check LaunchDarkly feature flag audit log for flips within 30 minutes of onset.",
                        "Correlate CPU utilization spike with inference timeout log signatures.",
                    ],
                    safe_remediation_steps=[
                        "Toggle off the newly enabled feature flag (enable_heavy_ml_reranker) immediately.",
                    ],
                )
            ],
            [
                PastIncidentSummary(
                    evidence_id="EV-PM-CF01",
                    past_incident_id="INC-20260722-011",
                    title="Recommendation Latency Spike After Enabling Unbatched Neural Reranker Flag",
                    service=service,
                    root_cause_category="config_flag_misfire",
                    summary="100% rollout of CPU-intensive reranker saturated worker nodes.",
                    resolution_applied="Toggled feature flag off via LaunchDarkly CLI in 90 seconds.",
                )
            ],
        )

    # TRAFFIC_SPIKE
    return (
        [
            RunbookDocument(
                evidence_id="EV-RUN-TS01",
                runbook_id="RB-EDG-009",
                title="Ingress Traffic Surge, HPA Ceiling & Rate-Limiting Runbook",
                service=service,
                symptom_tags=["traffic_spike", "high_rps", "autoscaling"],
                diagnostic_steps=[
                    "Verify whether any deployments or config changes occurred (expect zero changes).",
                    "Inspect ingress RPS multiplier and HPA currentReplicas vs maxReplicas.",
                ],
                safe_remediation_steps=[
                    "Scale frontend-edge deployment replicas (e.g., from 12 to 30 pods).",
                    "Enable adaptive token-bucket rate limiting on non-critical scraping endpoints.",
                ],
            )
        ],
        [
            PastIncidentSummary(
                evidence_id="EV-PM-TS01",
                past_incident_id="INC-20260830-021",
                title="Unannounced Promotional Campaign 4x Traffic Surge on Frontend Edge",
                service=service,
                root_cause_category="traffic_spike",
                summary="Flash sale traffic exceeded the static 12-replica HPA ceiling.",
                resolution_applied="Scaled replicas to 32 and enabled edge burst rate-limiting.",
            )
        ],
    )
