"""Remove all invoice runs except an explicit keep-list of live uploads.

Usage:
    uv run python scripts/prune_live_runs.py
    uv run python scripts/prune_live_runs.py --dry-run
    uv run python scripts/prune_live_runs.py --keep upload-abc,upload-def
"""

from __future__ import annotations

import argparse
import sys

# Default: the three live upload invoices under test (invoice_id prefix upload-).
DEFAULT_KEEP = (
    "upload-f49cc3f9",  # Acme Corporation — retail
    "upload-f7594a2c",  # Globex Industries — manufacturing
    "upload-5cc11b27",  # Northwind Traders — manufacturing
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune DB to only kept live upload invoices.")
    parser.add_argument(
        "--keep",
        help="Comma-separated invoice_ids to retain (default: project's three live uploads)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted only.")
    args = parser.parse_args()

    keep = set(DEFAULT_KEEP if not args.keep else [x.strip() for x in args.keep.split(",") if x.strip()])

    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import Document, Invoice, Run

    with session_scope() as session:
        all_invoices = session.query(Invoice).all()
        to_delete = [inv for inv in all_invoices if inv.invoice_id not in keep]
        kept = [inv for inv in all_invoices if inv.invoice_id in keep]

        print(f"Keeping {len(kept)} invoice(s): {[i.invoice_id for i in kept]}")
        print(f"Removing {len(to_delete)} invoice(s):")
        for inv in to_delete:
            runs = session.query(Run.run_id).filter_by(invoice_id=inv.invoice_id).all()
            print(f"  - {inv.invoice_id} ({inv.vendor_name_raw}) — {len(runs)} run(s)")

        if args.dry_run:
            print("Dry run — no changes committed.")
            return 0

        delete_invoice_ids = [inv.invoice_id for inv in to_delete]
        if delete_invoice_ids:
            # Delete runs first — Invoice.runs has no ORM cascade; DB CASCADE applies
            # only when the invoice row is removed via SQL, not via nulling FKs.
            deleted_runs = (
                session.query(Run)
                .filter(Run.invoice_id.in_(delete_invoice_ids))
                .delete(synchronize_session=False)
            )
            print(f"Deleted {deleted_runs} run(s).")
            for inv in to_delete:
                session.delete(inv)
            session.flush()

        kept_doc_ids = {inv.document_id for inv in kept}
        orphan_docs = (
            session.query(Document)
            .filter(Document.document_id.notin_(kept_doc_ids))
            .all()
        )
        print(f"Removing {len(orphan_docs)} orphan document row(s).")
        for doc in orphan_docs:
            session.delete(doc)

    print("Done. Restart the API process so in-memory run summaries drop stale entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
