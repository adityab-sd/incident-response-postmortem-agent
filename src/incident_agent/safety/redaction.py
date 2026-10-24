"""PII and secrets redaction filter for logs, alert payloads, and LLM inputs."""

from __future__ import annotations

import re
from typing import Any

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_API_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,})\b"
)
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|api_key|apikey|access_token|auth_token|client_secret)"
    r"(\s*[:=]\s*['\"]?)([^'\"\s,;]+)(['\"]?)"
)
_URI_CREDS_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:\s/]+:)([^@\s/]+)(@)")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")


class RedactionFilter:
    """Deterministic scrubber for secrets, tokens, emails, and IP addresses."""

    def __init__(
        self,
        *,
        redact_emails: bool = True,
        redact_tokens: bool = True,
        redact_ips: bool = True,
        preserve_localhost_ip: bool = True,
    ) -> None:
        self.redact_emails = redact_emails
        self.redact_tokens = redact_tokens
        self.redact_ips = redact_ips
        self.preserve_localhost_ip = preserve_localhost_ip

    def redact_text(self, text: str) -> str:
        """Scrub sensitive patterns from a plain string."""
        if not text:
            return text

        cleaned = text
        if self.redact_tokens:
            cleaned = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", cleaned)
            cleaned = _URI_CREDS_RE.sub(r"\1[REDACTED_SECRET]\3", cleaned)
            cleaned = _JWT_RE.sub("[REDACTED_JWT]", cleaned)
            cleaned = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", cleaned)
            cleaned = _AWS_KEY_RE.sub("[REDACTED_AWS_KEY]", cleaned)
            cleaned = _API_TOKEN_RE.sub("[REDACTED_TOKEN]", cleaned)
            cleaned = _KV_SECRET_RE.sub(r"\1\2[REDACTED_SECRET]\4", cleaned)

        if self.redact_emails:
            cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)

        if self.redact_ips:

            def _replace_ip(match: re.Match[str]) -> str:
                ip_val = match.group(0)
                if self.preserve_localhost_ip and ip_val in {"127.0.0.1", "0.0.0.0"}:
                    return ip_val
                return "[REDACTED_IP]"

            cleaned = _IPV4_RE.sub(_replace_ip, cleaned)

        return cleaned

    def redact_mapping(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact all string values in a dictionary."""
        result: dict[str, Any] = {}
        for key, value in data.items():
            lower_key = key.lower()
            if self.redact_tokens and any(
                s in lower_key for s in ("password", "secret", "token", "api_key", "authorization")
            ):
                result[key] = "[REDACTED_SECRET]"
            elif isinstance(value, str):
                result[key] = self.redact_text(value)
            elif isinstance(value, dict):
                result[key] = self.redact_mapping(value)
            elif isinstance(value, list):
                result[key] = [
                    self.redact_text(item)
                    if isinstance(item, str)
                    else self.redact_mapping(item)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result
