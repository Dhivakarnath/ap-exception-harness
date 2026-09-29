"""Escalation notifier seam.

When a run routes to a human, the approver is reached in their own tools —
Slack or email — but the auditable system of record is the in-app HITL review
queue (FR-11.3). This module is the *delivery* seam: a `Notifier` Protocol with
a console implementation for Slice 8 (and tests), so the graph can escalate
end-to-end in-process. Slice 9/10 supply the real Slack/email adapter and wire
the durable `HitlReview` row; both satisfy the same `notify` shape, so the graph
does not change when the channel does.

The console notifier does not pretend to deliver anywhere — it records the
escalation and returns a structured receipt. That keeps the demo honest: the
escalation happened and is inspectable, and nothing claims a Slack message was
sent when no Slack is configured.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Notifier(Protocol):
    """Delivers an escalation to a human approver's channel."""

    def notify(
        self,
        *,
        tenant_id: str,
        required_tier: str,
        reason: str,
        invoice_id: str,
        amount: str,
        currency: str,
    ) -> dict[str, object]:
        """Send the escalation. Returns a structured receipt for the audit log."""
        ...


class ConsoleNotifier:
    """Records escalations to an in-memory log and returns a receipt.

    The default for Slice 8 and tests. Deliberately does not claim delivery to
    any external channel — the receipt says `channel='console'` so a reviewer is
    never misled into thinking a Slack message went out when none did.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def notify(
        self,
        *,
        tenant_id: str,
        required_tier: str,
        reason: str,
        invoice_id: str,
        amount: str,
        currency: str,
    ) -> dict[str, object]:
        receipt: dict[str, object] = {
            "channel": "console",
            "tenant_id": tenant_id,
            "required_tier": required_tier,
            "reason": reason,
            "invoice_id": invoice_id,
            "amount": amount,
            "currency": currency,
            "at": datetime.now(UTC).isoformat(),
        }
        self.sent.append(receipt)
        return receipt


_DEFAULT_NOTIFIER = ConsoleNotifier()


def get_notifier() -> Notifier:
    """The process-default notifier.

    Console for now. When `notify_channel='slack'` is configured (and validated
    at startup in `config.py`), Slice 9 returns a Slack adapter here instead.
    """
    return _DEFAULT_NOTIFIER
