"""Persistence interface (`IncidentStore`) and SQLite implementation (`SQLiteIncidentStore`)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol

from incident_agent.schemas import (
    ActionExecutionRecord,
    ApprovalRecord,
    EvidenceRecord,
    IncidentState,
    LLMTrace,
)


class IncidentStore(Protocol):
    """Abstract persistence interface so SQLite can be swapped for PostgreSQL."""

    def save_state(self, state: IncidentState) -> None: ...

    def get_state(self, incident_id: str) -> IncidentState | None: ...

    def list_incidents(self, limit: int = 50) -> list[IncidentState]: ...

    def save_checkpoint(
        self, incident_id: str, step_name: str, round_number: int, state: IncidentState
    ) -> None: ...

    def get_latest_checkpoint(self, incident_id: str) -> tuple[str, int, IncidentState] | None: ...

    def save_evidence(self, evidence: EvidenceRecord) -> None: ...

    def get_evidence_for_incident(self, incident_id: str) -> list[EvidenceRecord]: ...

    def save_approval(self, approval: ApprovalRecord) -> None: ...

    def get_approval(self, incident_id: str, proposal_id: str) -> ApprovalRecord | None: ...

    def list_approvals(self, incident_id: str) -> list[ApprovalRecord]: ...

    def save_execution(self, execution: ActionExecutionRecord) -> None: ...

    def list_executions(self, incident_id: str) -> list[ActionExecutionRecord]: ...

    def save_trace(self, trace: LLMTrace) -> None: ...

    def list_traces(self, incident_id: str) -> list[LLMTrace]: ...


class SQLiteIncidentStore:
    """SQLite-backed implementation of `IncidentStore` with WAL journaling."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._persistent_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_conn.row_factory = sqlite3.Row
        else:
            path_obj = Path(self.db_path)
            path_obj.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        self._lock.acquire()
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _close_if_transient(self, conn: sqlite3.Connection) -> None:
        try:
            if self._persistent_conn is None:
                conn.close()
        finally:
            self._lock.release()

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS incidents (
                        incident_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        service TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        state_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS incident_checkpoints (
                        checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        incident_id TEXT NOT NULL,
                        step_name TEXT NOT NULL,
                        round_number INTEGER NOT NULL,
                        state_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_checkpoints_incident
                        ON incident_checkpoints (incident_id, checkpoint_id DESC);

                    CREATE TABLE IF NOT EXISTS evidence_records (
                        evidence_id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        service TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_evidence_incident
                        ON evidence_records (incident_id);

                    CREATE TABLE IF NOT EXISTS approval_records (
                        approval_id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL,
                        proposal_id TEXT NOT NULL,
                        approver_id TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        approved_at TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_approvals_incident_proposal
                        ON approval_records (incident_id, proposal_id);

                    CREATE TABLE IF NOT EXISTS action_executions (
                        execution_id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL,
                        proposal_id TEXT NOT NULL,
                        approver_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        executed_at TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS llm_traces (
                        trace_id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL,
                        subagent_name TEXT NOT NULL,
                        role TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        total_tokens INTEGER NOT NULL,
                        latency_ms REAL NOT NULL,
                        estimated_cost_usd REAL NOT NULL,
                        fallback_used INTEGER NOT NULL,
                        timestamp TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_traces_incident
                        ON llm_traces (incident_id);
                    """
                )
        finally:
            self._close_if_transient(conn)

    def save_state(self, state: IncidentState) -> None:
        conn = self._connect()
        try:
            service = state.intake.service if state.intake else "unknown"
            severity = state.intake.severity.value if state.intake else "SEV2"
            with conn:
                conn.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, status, service, severity, state_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(incident_id) DO UPDATE SET
                        status = excluded.status,
                        service = excluded.service,
                        severity = excluded.severity,
                        state_json = excluded.state_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        state.incident_id,
                        state.status.value,
                        service,
                        severity,
                        state.model_dump_json(),
                        state.created_at.isoformat(),
                        state.updated_at.isoformat(),
                    ),
                )
        finally:
            self._close_if_transient(conn)

    def get_state(self, incident_id: str) -> IncidentState | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_json FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                return None
            return IncidentState.model_validate_json(str(row["state_json"]))
        finally:
            self._close_if_transient(conn)

    def list_incidents(self, limit: int = 50) -> list[IncidentState]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT state_json FROM incidents ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [IncidentState.model_validate_json(str(r["state_json"])) for r in rows]
        finally:
            self._close_if_transient(conn)

    def save_checkpoint(
        self, incident_id: str, step_name: str, round_number: int, state: IncidentState
    ) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO incident_checkpoints (
                        incident_id, step_name, round_number, state_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        incident_id,
                        step_name,
                        round_number,
                        state.model_dump_json(),
                        datetime.now().isoformat(),
                    ),
                )
            self.save_state(state)
        finally:
            self._close_if_transient(conn)

    def get_latest_checkpoint(self, incident_id: str) -> tuple[str, int, IncidentState] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT step_name, round_number, state_json
                FROM incident_checkpoints
                WHERE incident_id = ?
                ORDER BY checkpoint_id DESC
                LIMIT 1
                """,
                (incident_id,),
            ).fetchone()
            if row is None:
                return None
            return (
                str(row["step_name"]),
                int(row["round_number"]),
                IncidentState.model_validate_json(str(row["state_json"])),
            )
        finally:
            self._close_if_transient(conn)

    def save_evidence(self, evidence: EvidenceRecord) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO evidence_records (
                        evidence_id, incident_id, source_type, service, timestamp, summary, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence.evidence_id,
                        evidence.incident_id,
                        evidence.source_type,
                        evidence.service,
                        evidence.timestamp.isoformat(),
                        evidence.summary,
                        evidence.model_dump_json(),
                    ),
                )
        finally:
            self._close_if_transient(conn)

    def get_evidence_for_incident(self, incident_id: str) -> list[EvidenceRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT record_json FROM evidence_records WHERE incident_id = ? ORDER BY timestamp ASC",
                (incident_id,),
            ).fetchall()
            return [EvidenceRecord.model_validate_json(str(r["record_json"])) for r in rows]
        finally:
            self._close_if_transient(conn)

    def save_approval(self, approval: ApprovalRecord) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO approval_records (
                        approval_id, incident_id, proposal_id, approver_id,
                        decision, reason, approved_at, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.approval_id,
                        approval.incident_id,
                        approval.proposal_id,
                        approval.approver_id,
                        approval.decision,
                        approval.reason,
                        approval.approved_at.isoformat(),
                        approval.model_dump_json(),
                    ),
                )
        finally:
            self._close_if_transient(conn)

    def get_approval(self, incident_id: str, proposal_id: str) -> ApprovalRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT record_json FROM approval_records
                WHERE incident_id = ? AND proposal_id = ?
                ORDER BY approved_at DESC
                LIMIT 1
                """,
                (incident_id, proposal_id),
            ).fetchone()
            if row is None:
                return None
            return ApprovalRecord.model_validate_json(str(row["record_json"]))
        finally:
            self._close_if_transient(conn)

    def list_approvals(self, incident_id: str) -> list[ApprovalRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT record_json FROM approval_records WHERE incident_id = ? ORDER BY approved_at ASC",
                (incident_id,),
            ).fetchall()
            return [ApprovalRecord.model_validate_json(str(r["record_json"])) for r in rows]
        finally:
            self._close_if_transient(conn)

    def save_execution(self, execution: ActionExecutionRecord) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO action_executions (
                        execution_id, incident_id, proposal_id, approver_id,
                        status, executed_at, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        execution.execution_id,
                        execution.incident_id,
                        execution.proposal_id,
                        execution.approver_id,
                        execution.status,
                        execution.executed_at.isoformat(),
                        execution.model_dump_json(),
                    ),
                )
        finally:
            self._close_if_transient(conn)

    def list_executions(self, incident_id: str) -> list[ActionExecutionRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT record_json FROM action_executions WHERE incident_id = ? ORDER BY executed_at ASC",
                (incident_id,),
            ).fetchall()
            return [ActionExecutionRecord.model_validate_json(str(r["record_json"])) for r in rows]
        finally:
            self._close_if_transient(conn)

    def save_trace(self, trace: LLMTrace) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO llm_traces (
                        trace_id, incident_id, subagent_name, role, provider, model_name,
                        total_tokens, latency_ms, estimated_cost_usd, fallback_used,
                        timestamp, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace.trace_id,
                        trace.incident_id,
                        trace.subagent_name,
                        trace.role.value,
                        trace.provider,
                        trace.model_name,
                        trace.total_tokens,
                        trace.latency_ms,
                        trace.estimated_cost_usd,
                        1 if trace.fallback_used else 0,
                        trace.timestamp.isoformat(),
                        trace.model_dump_json(),
                    ),
                )
        finally:
            self._close_if_transient(conn)

    def list_traces(self, incident_id: str) -> list[LLMTrace]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT record_json FROM llm_traces WHERE incident_id = ? ORDER BY timestamp ASC",
                (incident_id,),
            ).fetchall()
            return [LLMTrace.model_validate_json(str(r["record_json"])) for r in rows]
        finally:
            self._close_if_transient(conn)
