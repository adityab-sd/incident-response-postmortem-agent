"""Structured JSON logging with per-incident context binding."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_incident_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "incident_id", default="SYSTEM"
)


def set_incident_context(incident_id: str) -> contextvars.Token[str]:
    """Bind an incident_id to the current async/execution context."""
    return _incident_id_ctx.set(incident_id)


def reset_incident_context(token: contextvars.Token[str]) -> None:
    """Restore previous incident_id context."""
    _incident_id_ctx.reset(token)


def get_incident_context() -> str:
    """Return currently bound incident_id."""
    return _incident_id_ctx.get()


class StructuredJsonFormatter(logging.Formatter):
    """Formats every log record as a single-line JSON object with `incident_id`."""

    def format(self, record: logging.LogRecord) -> str:
        incident_id = getattr(record, "incident_id", None) or get_incident_context()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "incident_id": incident_id,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "structured_data", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with structured JSON output."""
    root = logging.getLogger("incident_agent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not any(isinstance(h.formatter, StructuredJsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        root.handlers = [handler]
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a configured child logger under `incident_agent`."""
    configure_logging()
    if not name.startswith("incident_agent"):
        name = f"incident_agent.{name}"
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    incident_id: str | None = None,
    **kwargs: Any,
) -> None:
    """Emit a structured JSON log line with explicit incident_id and metadata."""
    resolved_id = incident_id or get_incident_context()
    logger.log(
        level,
        message,
        extra={"incident_id": resolved_id, "structured_data": kwargs},
    )
