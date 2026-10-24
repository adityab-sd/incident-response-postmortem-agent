"""Adapter protocols and base exceptions for observability and infrastructure data sources."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from incident_agent.schemas import (
    ChangeEvent,
    LogCluster,
    LogEntry,
    MetricAnomaly,
    MetricSeries,
    PastIncidentSummary,
    RunbookDocument,
    ScenarioType,
    ServiceTopology,
)


class AdapterUnavailableError(RuntimeError):
    """Raised when an observability or infrastructure data source is unreachable or times out."""

    def __init__(self, source_name: str, message: str = "Backend connection timeout") -> None:
        super().__init__(f"Data source '{source_name}' unavailable: {message}")
        self.source_name = source_name


class LogAdapter(Protocol):
    """Loki/Elasticsearch-style read-only log query interface."""

    def query_logs(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
        *,
        severity_filter: str = "ERROR",
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
        limit: int = 100,
    ) -> list[LogEntry]: ...

    def get_error_clusters(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> list[LogCluster]: ...


class MetricsAdapter(Protocol):
    """Prometheus-style read-only metric query interface."""

    def query_range(
        self,
        service: str,
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
        *,
        step_seconds: int = 60,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> MetricSeries: ...

    def detect_anomalies(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> list[MetricAnomaly]: ...


class ChangeAdapter(Protocol):
    """Deploy, config, and feature-flag history query interface."""

    def get_recent_changes(
        self,
        services: list[str],
        start_time: datetime,
        end_time: datetime,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> list[ChangeEvent]: ...


class TopologyAdapter(Protocol):
    """Service mesh / catalog dependency topology query interface."""

    def get_service_topology(
        self,
        service: str,
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
    ) -> ServiceTopology: ...


class RunbookAdapter(Protocol):
    """Searchable runbook and historical post-mortem store interface."""

    def search_runbooks(
        self,
        service: str,
        symptoms: list[str],
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
        top_k: int = 3,
    ) -> list[RunbookDocument]: ...

    def search_past_postmortems(
        self,
        service: str,
        symptoms: list[str],
        *,
        scenario: ScenarioType = ScenarioType.BAD_DEPLOY,
        top_k: int = 3,
    ) -> list[PastIncidentSummary]: ...
