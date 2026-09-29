"""Query scope for runs that processed a real uploaded document.

The Runs queue and headline run counts only include invoices whose extraction
wrote `field_provenance` as a JSON object. Failed or scripted rows without that
provenance stay in the audit tables but are excluded from operator-facing history
and matching KPI totals.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func


def document_processed_runs_query(session: Any, *, tenant_id: str) -> Any:
    """Base query for runs with persisted extraction provenance, tenant-scoped."""
    from ap_agent.persistence.models import Invoice, Run
    from ap_agent.tenants import ALL_TENANTS

    query = (
        session.query(Run)
        .join(Invoice, Invoice.invoice_id == Run.invoice_id)
        .filter(func.jsonb_typeof(Invoice.field_provenance) == "object")
    )
    if tenant_id != ALL_TENANTS:
        query = query.filter(Run.tenant_id == tenant_id)
    return query
