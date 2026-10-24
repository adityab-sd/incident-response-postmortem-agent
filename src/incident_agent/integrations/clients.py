"""Slack and PagerDuty integration clients with timeout, exponential backoff, and jitter."""

from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from incident_agent.config import AppConfig, get_config
from incident_agent.logging_utils import get_logger, log_event
from incident_agent.reporting.builders import build_slack_blocks
from incident_agent.schemas import TriageSummaryReport

logger = get_logger("integrations.clients")


@dataclass
class PostedSlackMessage:
    """Record of a Slack message sent by `SlackClient`."""

    channel: str
    text: str
    thread_ts: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    posted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PagerDutyEventRecord:
    """Record of an outbound PagerDuty API update."""

    incident_id: str
    action: str  # acknowledge | add_note
    note: str
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any],
    timeout_seconds: float = 5.0,
    max_retries: int = 2,
    base_delay: float = 0.05,
    jitter: float = 0.02,
    incident_id: str = "SYSTEM",
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Perform an HTTP request with timeout and exponential backoff with jitter."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
                resp = client.request(method, url, headers=headers, json=json_body)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else {"ok": True}
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = base_delay * (2**attempt) + random.uniform(0.0, jitter)
            log_event(
                logger,
                logging.WARNING,
                "Retrying integration HTTP call after transient failure",
                incident_id=incident_id,
                url=url,
                attempt=attempt + 1,
                error=str(exc),
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


class SlackClient:
    """Slack Web API client for acknowledgments, triage summaries, and post-mortem drafts."""

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        base_url: str | None = None,
        live_http: bool = False,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or get_config()
        self.base_url = (base_url or self.config.env.slack_api_base_url).rstrip("/")
        self.live_http = live_http
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.sent_messages: list[PostedSlackMessage] = []

    def post_acknowledgment(
        self,
        *,
        incident_id: str,
        service: str,
        severity: str,
        channel: str | None = None,
    ) -> str:
        """Post initial incident acknowledgment and return Slack `thread_ts`."""
        target_channel = channel or self.config.env.slack_default_channel
        thread_ts = f"{int(time.time())}.{uuid.uuid4().hex[:6]}"
        text = (
            f"🚨 *Incident Response Pod Acknowledged `{incident_id}`*\n"
            f"Investigating `{service}` (`{severity}`). Sub-agents querying logs, metrics, "
            f"deploys, topology, and runbooks in parallel..."
        )
        if self.live_http:
            res = _request_with_retry(
                "POST",
                f"{self.base_url}/chat.postMessage",
                headers={"Authorization": f"Bearer {self.config.env.slack_bot_token}"},
                json_body={"channel": target_channel, "text": text},
                timeout_seconds=self.timeout_seconds,
                incident_id=incident_id,
                transport=self.transport,
            )
            thread_ts = str(res.get("ts") or thread_ts)

        msg = PostedSlackMessage(channel=target_channel, text=text, thread_ts=thread_ts)
        self.sent_messages.append(msg)
        log_event(
            logger,
            logging.INFO,
            "Posted Slack incident acknowledgment",
            incident_id=incident_id,
            channel=target_channel,
            thread_ts=thread_ts,
        )
        return thread_ts

    def post_triage_summary(
        self,
        report: TriageSummaryReport,
        *,
        channel: str | None = None,
        thread_ts: str | None = None,
    ) -> PostedSlackMessage:
        """Post structured triage summary with interactive Approve/Reject proposal buttons."""
        target_channel = channel or self.config.env.slack_default_channel
        resolved_ts = thread_ts or f"{int(time.time())}.000100"
        blocks = build_slack_blocks(report)
        text = report.slack_markdown

        if self.live_http:
            _request_with_retry(
                "POST",
                f"{self.base_url}/chat.postMessage",
                headers={"Authorization": f"Bearer {self.config.env.slack_bot_token}"},
                json_body={
                    "channel": target_channel,
                    "thread_ts": resolved_ts,
                    "text": text,
                    "blocks": blocks,
                },
                timeout_seconds=self.timeout_seconds,
                incident_id=report.incident_id,
                transport=self.transport,
            )

        msg = PostedSlackMessage(
            channel=target_channel,
            text=text,
            thread_ts=resolved_ts,
            blocks=blocks,
        )
        self.sent_messages.append(msg)
        log_event(
            logger,
            logging.INFO,
            "Posted Slack triage summary to incident thread",
            incident_id=report.incident_id,
            channel=target_channel,
            thread_ts=resolved_ts,
        )
        return msg

    def post_postmortem(
        self,
        *,
        incident_id: str,
        markdown_body: str,
        channel: str | None = None,
        thread_ts: str | None = None,
    ) -> PostedSlackMessage:
        """Post the completed blameless post-mortem draft to the Slack incident channel."""
        target_channel = channel or self.config.env.slack_default_channel
        resolved_ts = thread_ts or f"{int(time.time())}.000200"

        if self.live_http:
            _request_with_retry(
                "POST",
                f"{self.base_url}/chat.postMessage",
                headers={"Authorization": f"Bearer {self.config.env.slack_bot_token}"},
                json_body={
                    "channel": target_channel,
                    "thread_ts": resolved_ts,
                    "text": markdown_body,
                },
                timeout_seconds=self.timeout_seconds,
                incident_id=incident_id,
                transport=self.transport,
            )

        msg = PostedSlackMessage(
            channel=target_channel,
            text=markdown_body,
            thread_ts=resolved_ts,
        )
        self.sent_messages.append(msg)
        log_event(
            logger,
            logging.INFO,
            "Posted blameless post-mortem draft to Slack",
            incident_id=incident_id,
            channel=target_channel,
            thread_ts=resolved_ts,
        )
        return msg


class PagerDutyClient:
    """PagerDuty REST API client for acknowledging incidents and attaching triage notes."""

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        base_url: str | None = None,
        live_http: bool = False,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or get_config()
        self.base_url = (base_url or self.config.env.pagerduty_api_base_url).rstrip("/")
        self.live_http = live_http
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.events: list[PagerDutyEventRecord] = []

    def acknowledge_incident(self, incident_id: str, external_id: str) -> PagerDutyEventRecord:
        """Send an acknowledgment update to PagerDuty."""
        if self.live_http:
            _request_with_retry(
                "PUT",
                f"{self.base_url}/incidents/{external_id}",
                headers={"Authorization": f"Token token={self.config.env.pagerduty_api_key}"},
                json_body={
                    "incident": {
                        "type": "incident_reference",
                        "status": "acknowledged",
                    }
                },
                timeout_seconds=self.timeout_seconds,
                incident_id=incident_id,
                transport=self.transport,
            )
        rec = PagerDutyEventRecord(
            incident_id=incident_id,
            action="acknowledge",
            note=f"Acknowledged external PagerDuty incident {external_id}",
        )
        self.events.append(rec)
        return rec

    def add_incident_note(
        self, incident_id: str, external_id: str, note: str
    ) -> PagerDutyEventRecord:
        """Attach a triage summary note to a PagerDuty incident."""
        if self.live_http:
            _request_with_retry(
                "POST",
                f"{self.base_url}/incidents/{external_id}/notes",
                headers={"Authorization": f"Token token={self.config.env.pagerduty_api_key}"},
                json_body={"note": {"content": note}},
                timeout_seconds=self.timeout_seconds,
                incident_id=incident_id,
                transport=self.transport,
            )
        rec = PagerDutyEventRecord(
            incident_id=incident_id,
            action="add_note",
            note=note,
        )
        self.events.append(rec)
        return rec
