"""Runtime dependency container shared across the 11 sub-agent tools."""

from __future__ import annotations

from incident_agent.adapters.mock_adapters import AdapterBundle
from incident_agent.config import AppConfig, get_config
from incident_agent.persistence.store import IncidentStore, SQLiteIncidentStore
from incident_agent.safety.injection import InjectionFilter
from incident_agent.safety.redaction import RedactionFilter
from incident_agent.schemas import EvidenceRecord
from incident_agent.tracing.tracer import LLMTracer


class SubagentRuntimeContext:
    """Holds adapters, safety filters, persistence store, and LLM tracer for tool execution."""

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        adapters: AdapterBundle | None = None,
        store: IncidentStore | None = None,
        tracer: LLMTracer | None = None,
        redactor: RedactionFilter | None = None,
        injector: InjectionFilter | None = None,
    ) -> None:
        self.config = config or get_config()
        self.adapters = adapters or AdapterBundle()
        self.store = store or SQLiteIncidentStore(":memory:")
        self.tracer = tracer or LLMTracer(store=self.store)
        safety_cfg = self.config.thresholds.safety
        self.redactor = redactor or RedactionFilter(
            redact_emails=safety_cfg.redact_emails,
            redact_tokens=safety_cfg.redact_tokens,
            redact_ips=safety_cfg.redact_ips,
            preserve_localhost_ip=safety_cfg.preserve_localhost_ip,
        )
        self.injector = injector or InjectionFilter(enabled=safety_cfg.strip_prompt_injections)

    def sanitize_and_redact(self, text: str, *, incident_id: str) -> tuple[str, int]:
        """Redact secrets/PII and strip prompt-injection patterns from untrusted text."""
        redacted = self.redactor.redact_text(text)
        inj_res = self.injector.sanitize(redacted, incident_id=incident_id)
        return inj_res.sanitized_text, len(inj_res.matched_rules)

    def record_evidence(self, evidence: EvidenceRecord) -> None:
        """Persist a verified evidence record to the store."""
        self.store.save_evidence(evidence)
