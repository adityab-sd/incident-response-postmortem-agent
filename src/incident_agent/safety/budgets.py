"""Hard step, round, and token budget enforcement per incident."""

from __future__ import annotations

import logging

from incident_agent.logging_utils import get_logger, log_event

logger = get_logger("safety.budgets")


class BudgetExceededError(RuntimeError):
    """Raised when an incident investigation reaches its configured step or token budget."""

    def __init__(self, incident_id: str, reason: str) -> None:
        super().__init__(f"Budget exceeded for incident {incident_id}: {reason}")
        self.incident_id = incident_id
        self.reason = reason


class BudgetEnforcer:
    """Tracks and enforces tool-call, token, and round limits for an incident."""

    def __init__(
        self,
        *,
        max_tool_calls: int = 25,
        max_tokens: int = 100000,
        max_rounds: int = 3,
    ) -> None:
        self.max_tool_calls = max_tool_calls
        self.max_tokens = max_tokens
        self.max_rounds = max_rounds

    def check_can_proceed(
        self,
        *,
        incident_id: str,
        current_tool_calls: int,
        current_tokens: int,
        current_round: int,
        next_tool_name: str = "subagent",
    ) -> None:
        """Verify that executing another tool call will not violate configured caps."""
        if current_round > self.max_rounds:
            reason = f"Investigation round {current_round} exceeds max_rounds ({self.max_rounds})"
            log_event(
                logger,
                logging.WARNING,
                "Investigation halted by round budget",
                incident_id=incident_id,
                current_round=current_round,
                max_rounds=self.max_rounds,
            )
            raise BudgetExceededError(incident_id, reason)

        if current_tool_calls >= self.max_tool_calls:
            reason = (
                f"Tool call count {current_tool_calls} reached max_tool_calls "
                f"({self.max_tool_calls}) before calling {next_tool_name}"
            )
            log_event(
                logger,
                logging.WARNING,
                "Investigation halted by tool call budget",
                incident_id=incident_id,
                current_tool_calls=current_tool_calls,
                max_tool_calls=self.max_tool_calls,
                next_tool=next_tool_name,
            )
            raise BudgetExceededError(incident_id, reason)

        if current_tokens >= self.max_tokens:
            reason = (
                f"Token usage {current_tokens} reached max_tokens "
                f"({self.max_tokens}) before calling {next_tool_name}"
            )
            log_event(
                logger,
                logging.WARNING,
                "Investigation halted by token budget",
                incident_id=incident_id,
                current_tokens=current_tokens,
                max_tokens=self.max_tokens,
                next_tool=next_tool_name,
            )
            raise BudgetExceededError(incident_id, reason)
