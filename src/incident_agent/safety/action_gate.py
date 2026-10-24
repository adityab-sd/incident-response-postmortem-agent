"""Mandatory human approval gate for any production-mutating remediation action."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from incident_agent.logging_utils import get_logger, log_event
from incident_agent.schemas import ActionExecutionRecord, ApprovalRecord, RemediationProposal

if TYPE_CHECKING:
    from incident_agent.persistence.store import IncidentStore

logger = get_logger("safety.action_gate")


class UnapprovedActionError(PermissionError):
    """Raised when `execute_approved_action` is invoked without a recorded human approval."""

    def __init__(self, incident_id: str, proposal_id: str, reason: str) -> None:
        super().__init__(
            f"Refusing to execute action proposal '{proposal_id}' on incident '{incident_id}': {reason}"
        )
        self.incident_id = incident_id
        self.proposal_id = proposal_id
        self.reason = reason


class ActionGate:
    """Enforces human-in-the-loop approval before executing any remediation proposal."""

    def __init__(self, store: IncidentStore) -> None:
        self.store = store

    def record_approval(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        approver_id: str,
        decision: str = "approved",
        reason: str = "Approved via Slack interactive button",
    ) -> ApprovalRecord:
        """Record an explicit human approval or rejection decision."""
        if not approver_id or not approver_id.strip():
            raise ValueError("approver_id must be a non-empty human user identifier")
        normalized_decision = decision.lower().strip()
        if normalized_decision not in {"approved", "rejected"}:
            raise ValueError(f"Invalid approval decision: {decision}")

        record = ApprovalRecord(
            approval_id=f"APR-{uuid.uuid4().hex[:8].upper()}",
            incident_id=incident_id,
            proposal_id=proposal_id,
            approver_id=approver_id.strip(),
            decision=normalized_decision,
            reason=reason,
            approved_at=datetime.now(UTC),
        )
        self.store.save_approval(record)
        log_event(
            logger,
            logging.INFO,
            f"Recorded human {normalized_decision} for remediation proposal",
            incident_id=incident_id,
            proposal_id=proposal_id,
            approver_id=record.approver_id,
            approval_id=record.approval_id,
        )
        return record

    def execute_approved_action(
        self,
        *,
        incident_id: str,
        proposal: RemediationProposal,
    ) -> ActionExecutionRecord:
        """Execute a remediation proposal ONLY if a recorded human approval exists."""
        approval = self.store.get_approval(incident_id, proposal.proposal_id)
        if approval is None:
            log_event(
                logger,
                logging.ERROR,
                "Blocked unapproved remediation execution attempt",
                incident_id=incident_id,
                proposal_id=proposal.proposal_id,
            )
            raise UnapprovedActionError(
                incident_id,
                proposal.proposal_id,
                "No human approval record found in store.",
            )

        if approval.decision != "approved":
            log_event(
                logger,
                logging.ERROR,
                "Blocked rejected remediation execution attempt",
                incident_id=incident_id,
                proposal_id=proposal.proposal_id,
                decision=approval.decision,
            )
            raise UnapprovedActionError(
                incident_id,
                proposal.proposal_id,
                f"Proposal decision is '{approval.decision}', not 'approved'.",
            )

        execution = ActionExecutionRecord(
            execution_id=f"EXEC-{uuid.uuid4().hex[:8].upper()}",
            incident_id=incident_id,
            proposal_id=proposal.proposal_id,
            approver_id=approval.approver_id,
            action_type=proposal.action_type,
            target_service=proposal.target_service,
            command_executed=proposal.command_or_api_call,
            status="executed",
            output=(
                f"[SIMULATED_EXECUTION_SUCCESS] Executed '{proposal.command_or_api_call}' "
                f"on '{proposal.target_service}' with human approval {approval.approval_id} "
                f"by {approval.approver_id}."
            ),
            executed_at=datetime.now(UTC),
        )
        self.store.save_execution(execution)
        log_event(
            logger,
            logging.INFO,
            "Executed human-approved remediation action",
            incident_id=incident_id,
            proposal_id=proposal.proposal_id,
            execution_id=execution.execution_id,
            approver_id=approval.approver_id,
        )
        return execution


def execute_approved_action(
    store: IncidentStore,
    incident_id: str,
    proposal: RemediationProposal,
) -> ActionExecutionRecord:
    """Module-level convenience function enforcing the human approval gate."""
    gate = ActionGate(store)
    return gate.execute_approved_action(incident_id=incident_id, proposal=proposal)
