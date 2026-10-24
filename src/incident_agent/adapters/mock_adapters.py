"""Mock observability and infrastructure adapters backed by the 5-scenario library."""

from __future__ import annotations

from datetime import datetime, timedelta

from incident_agent.adapters.base import AdapterUnavailableError
from incident_agent.adapters.scenarios import (
    build_scenario_changes,
    build_scenario_logs,
    build_scenario_metrics,
    build_scenario_runbooks,
    build_scenario_topology,
)
from incident_agent.safety.allowlist import DataSourceAllowlist
from incident_agent.schemas import (
    ChangeEvent,
    LogCluster,
    LogEntry,
    MetricAnomaly,
    MetricPoint,
    MetricSeries,
    PastIncidentSummary,
    RunbookDocument,
    ScenarioType,
    ServiceTopology,
)


class MockLogAdapter:
    """Mock Loki/Elasticsearch log adapter."""

    def __init__(
        self,
        *,
        allowlist: DataSourceAllowlist | None = None,
        fail: bool = False,
        large_bundle: bool = False,
        injected_log_messages: list[str] | None = None,
    ) -> None:
        self.allowlist = allowlist or DataSourceAllowlist()
        self.fail = fail
        self.large_bundle = large_bundle
        self.injected_log_messages = injected_log_messages or []

    def query_logs(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
        *,
        severity_filter: str = "ERROR",
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
        limit: int = 100,
    ) -> list[LogEntry]:
        del start_time, severity_filter
        self.allowlist.verify("logs", mode="read")
        if self.fail:
            raise AdapterUnavailableError("logs", "Loki cluster query timeout after 15.0s")

        entries, _ = build_scenario_logs(
            scenario, service, end_time, large_bundle=self.large_bundle
        )
        for idx, extra_msg in enumerate(self.injected_log_messages):
            entries.insert(
                0,
                LogEntry(
                    log_id=f"LOG-INJ-{idx + 1:03d}",
                    timestamp=end_time - timedelta(minutes=5),
                    service=service,
                    level="ERROR",
                    message=extra_msg,
                    trace_id=f"tr-inj-{idx + 1}",
                ),
            )
        if self.large_bundle:
            return entries
        return entries[:limit]

    def get_error_clusters(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> list[LogCluster]:
        del start_time
        self.allowlist.verify("logs", mode="read")
        if self.fail:
            raise AdapterUnavailableError("logs", "Loki cluster query timeout after 15.0s")

        _, clusters = build_scenario_logs(
            scenario, service, end_time, large_bundle=self.large_bundle
        )
        return clusters


class MockMetricsAdapter:
    """Mock Prometheus metrics adapter."""

    def __init__(
        self,
        *,
        allowlist: DataSourceAllowlist | None = None,
        fail: bool = False,
    ) -> None:
        self.allowlist = allowlist or DataSourceAllowlist()
        self.fail = fail

    def query_range(
        self,
        service: str,
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
        *,
        step_seconds: int = 60,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> MetricSeries:
        self.allowlist.verify("metrics", mode="read")
        if self.fail:
            raise AdapterUnavailableError("metrics", "Prometheus TSDB HTTP 503 Unavailable")

        anomalies = build_scenario_metrics(scenario, service, end_time)
        primary = next((a for a in anomalies if a.metric_name == metric_name), anomalies[0])
        points: list[MetricPoint] = []
        cursor = start_time
        while cursor <= end_time:
            val = primary.peak_value if cursor >= primary.onset_time else primary.baseline_value
            points.append(MetricPoint(timestamp=cursor, value=val))
            cursor += timedelta(seconds=max(step_seconds, 60))
        return MetricSeries(
            service=service,
            metric_name=primary.metric_name,
            unit=primary.unit,
            points=points,
        )

    def detect_anomalies(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> list[MetricAnomaly]:
        del start_time
        self.allowlist.verify("metrics", mode="read")
        if self.fail:
            raise AdapterUnavailableError("metrics", "Prometheus TSDB HTTP 503 Unavailable")
        return build_scenario_metrics(scenario, service, end_time)


class MockChangeAdapter:
    """Mock deploy, config, and feature-flag history adapter."""

    def __init__(
        self,
        *,
        allowlist: DataSourceAllowlist | None = None,
        fail: bool = False,
    ) -> None:
        self.allowlist = allowlist or DataSourceAllowlist()
        self.fail = fail

    def get_recent_changes(
        self,
        services: list[str],
        start_time: datetime,
        end_time: datetime,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> list[ChangeEvent]:
        del services, start_time
        self.allowlist.verify("deploys", mode="read")
        if self.fail:
            raise AdapterUnavailableError("deploys", "Deployment history API unreachable")
        return build_scenario_changes(scenario, end_time)


class MockTopologyAdapter:
    """Mock service dependency graph adapter."""

    def __init__(
        self,
        *,
        allowlist: DataSourceAllowlist | None = None,
        fail: bool = False,
    ) -> None:
        self.allowlist = allowlist or DataSourceAllowlist()
        self.fail = fail

    def get_service_topology(
        self,
        service: str,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> ServiceTopology:
        self.allowlist.verify("topology", mode="read")
        if self.fail:
            raise AdapterUnavailableError("topology", "Service catalog graph timeout")
        return build_scenario_topology(scenario, service)


class MockRunbookAdapter:
    """Mock operational runbook and past post-mortem search adapter."""

    def __init__(
        self,
        *,
        allowlist: DataSourceAllowlist | None = None,
        fail: bool = False,
    ) -> None:
        self.allowlist = allowlist or DataSourceAllowlist()
        self.fail = fail

    def search_runbooks(
        self,
        service: str,
        symptoms: list[str],
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
        top_k: int = 3,
    ) -> list[RunbookDocument]:
        del symptoms
        self.allowlist.verify("runbooks", mode="read")
        if self.fail:
            raise AdapterUnavailableError("runbooks", "Runbook search index offline")
        runbooks, _ = build_scenario_runbooks(scenario, service)
        return runbooks[:top_k]

    def search_past_postmortems(
        self,
        service: str,
        symptoms: list[str],
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
        top_k: int = 3,
    ) -> list[PastIncidentSummary]:
        del symptoms
        self.allowlist.verify("runbooks", mode="read")
        if self.fail:
            raise AdapterUnavailableError("runbooks", "Runbook search index offline")
        _, postmortems = build_scenario_runbooks(scenario, service)
        return postmortems[:top_k]


class AdapterBundle:
    """Container holding all 5 observability and infrastructure adapters."""

    def __init__(
        self,
        *,
        logs: MockLogAdapter | None = None,
        metrics: MockMetricsAdapter | None = None,
        changes: MockChangeAdapter | None = None,
        topology: MockTopologyAdapter | None = None,
        runbooks: MockRunbookAdapter | None = None,
    ) -> None:
        self.logs = logs or MockLogAdapter()
        self.metrics = metrics or MockMetricsAdapter()
        self.changes = changes or MockChangeAdapter()
        self.topology = topology or MockTopologyAdapter()
        self.runbooks = runbooks or MockRunbookAdapter()
