"""Prompt-injection defense filter for untrusted log lines, alerts, and runbook content."""

from __future__ import annotations

import logging
import re

from incident_agent.logging_utils import get_logger, log_event

logger = get_logger("safety.injection")

_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_instructions",
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|above|system)\s+"
            r"(?:instructions|prompts|rules|directives)\b"
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"(?i)(?:<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]|<<SYS>>|"
            r"###\s*(?:System|Instruction|Assistant)\s*:|^\s*system\s*prompt\s*:)"
        ),
    ),
    (
        "action_bypass",
        re.compile(
            r"(?i)\b(?:execute_approved_action|auto_approve|bypass_approval|"
            r"approve_all_actions|rm\s+-rf\s+/|DROP\s+TABLE)\b"
        ),
    ),
    (
        "you_are_now",
        re.compile(
            r"(?i)\byou\s+are\s+now\s+(?:a|an|in)\s+(?:unrestricted|developer|admin|sudo|root)\b"
        ),
    ),
]


class InjectionSanitizationResult:
    """Result of scanning and neutralizing untrusted text."""

    def __init__(self, sanitized_text: str, matched_rules: list[str]) -> None:
        self.sanitized_text = sanitized_text
        self.matched_rules = matched_rules

    @property
    def injection_detected(self) -> bool:
        return len(self.matched_rules) > 0


class InjectionFilter:
    """Neutralizes embedded instructions in untrusted telemetry and wraps data safely."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def sanitize(self, text: str, *, incident_id: str | None = None) -> InjectionSanitizationResult:
        """Neutralize prompt-injection directives inside untrusted text."""
        if not self.enabled or not text:
            return InjectionSanitizationResult(text, [])

        cleaned = text
        matched_rules: list[str] = []

        for rule_name, pattern in _INJECTION_PATTERNS:
            if pattern.search(cleaned):
                matched_rules.append(rule_name)
                cleaned = pattern.sub(f"[SANITIZED_INJECTION:{rule_name}]", cleaned)

        # Escape XML delimiter tags so untrusted text cannot break out of data blocks
        cleaned = cleaned.replace("</untrusted_telemetry>", "&lt;/untrusted_telemetry&gt;")
        cleaned = cleaned.replace("<untrusted_telemetry>", "&lt;untrusted_telemetry&gt;")

        if matched_rules:
            log_event(
                logger,
                logging.WARNING,
                "Prompt injection pattern neutralized in untrusted input",
                incident_id=incident_id,
                matched_rules=matched_rules,
            )

        return InjectionSanitizationResult(cleaned, matched_rules)

    def wrap_untrusted_block(
        self, text: str, *, source_label: str, incident_id: str | None = None
    ) -> str:
        """Sanitize text and enclose it in explicit read-only data delimiters."""
        result = self.sanitize(text, incident_id=incident_id)
        return (
            f'<untrusted_telemetry source="{source_label}">\n'
            f"{result.sanitized_text}\n"
            f"</untrusted_telemetry>"
        )
