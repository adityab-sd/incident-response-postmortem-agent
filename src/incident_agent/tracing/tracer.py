"""LLM call tracing and token cost estimation."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from incident_agent.logging_utils import get_logger, log_event
from incident_agent.schemas import LLMTrace, ModelRole

if TYPE_CHECKING:
    from incident_agent.persistence.store import IncidentStore

logger = get_logger("tracing.tracer")


def estimate_token_count(text: str) -> int:
    """Estimate token count (~4 characters per token, minimum 1 for non-empty text)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def compute_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_cost_per_1m_usd: float,
    output_cost_per_1m_usd: float,
) -> float:
    """Compute estimated LLM invocation cost in USD."""
    in_cost = (input_tokens / 1_000_000.0) * input_cost_per_1m_usd
    out_cost = (output_tokens / 1_000_000.0) * output_cost_per_1m_usd
    return round(in_cost + out_cost, 8)


class LLMTracer:
    """Records per-call LLM telemetry and aggregates incident token/cost usage."""

    def __init__(self, store: IncidentStore | None = None) -> None:
        self.store = store
        self._memory_traces: list[LLMTrace] = []

    def record_call(
        self,
        *,
        incident_id: str,
        subagent_name: str,
        role: ModelRole,
        provider: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        input_cost_per_1m_usd: float = 0.1,
        output_cost_per_1m_usd: float = 0.2,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
    ) -> LLMTrace:
        """Create, log, and persist a structured `LLMTrace` entry."""
        total_tokens = input_tokens + output_tokens
        cost_usd = compute_cost_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_per_1m_usd=input_cost_per_1m_usd,
            output_cost_per_1m_usd=output_cost_per_1m_usd,
        )
        trace = LLMTrace(
            trace_id=f"TRC-{uuid.uuid4().hex[:10].upper()}",
            incident_id=incident_id,
            subagent_name=subagent_name,
            role=role,
            provider=provider,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=round(latency_ms, 2),
            estimated_cost_usd=cost_usd,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            timestamp=datetime.now(UTC),
        )
        self._memory_traces.append(trace)
        if self.store is not None:
            self.store.save_trace(trace)

        log_event(
            logger,
            logging.INFO,
            "Recorded LLM call trace",
            incident_id=incident_id,
            trace_id=trace.trace_id,
            subagent=subagent_name,
            role=role.value,
            provider=provider,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=trace.latency_ms,
            cost_usd=cost_usd,
            fallback_used=fallback_used,
        )
        return trace

    def get_traces(self, incident_id: str | None = None) -> list[LLMTrace]:
        """Return recorded traces, optionally filtered by incident_id."""
        if incident_id is not None and self.store is not None:
            stored = self.store.list_traces(incident_id)
            if stored:
                return stored
        if incident_id is None:
            return list(self._memory_traces)
        return [t for t in self._memory_traces if t.incident_id == incident_id]
