import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ReviewActions } from "@/components/HitlQueue";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState, Panel } from "@/components/ui/Panel";
import { ALL_TENANTS, apiTenantParam, tenantShortLabel } from "@/lib/tenants";
import { useTenantFilter } from "@/store/useTenantFilter";
import { listHitlReviews, type HitlReviewSummary } from "@/transport/api";

/**
 * Durable HITL queue — reads pending reviews from Postgres, not process memory.
 * Each row can approve, correct the GL account, or reject; the run id still
 * opens the full case.
 */
export function ReviewsPage() {
  const { tenant } = useTenantFilter();
  const [reviews, setReviews] = useState<HitlReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const showTenant = tenant === ALL_TENANTS;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReviews(await listHitlReviews(apiTenantParam(tenant), "pending"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load reviews");
    } finally {
      setLoading(false);
    }
  }, [tenant]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(id);
  }, [refresh]);

  return (
    <PageBody className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Human in the loop"
        title="Review queue"
        description="Pending approvals persisted in the audit database. Reviews survive API restarts and expire after the configured window if unactioned. Scope follows the View tenant selector in the sidebar."
        actions={
          <Button variant="secondary" onClick={() => void refresh()}>
            Refresh
          </Button>
        }
      />

      {error && (
        <div role="alert" className="rounded-lg border border-fail/40 bg-fail-soft px-4 py-2.5 text-sm text-fail-ink">
          {error}
        </div>
      )}

      <Panel title="Pending reviews" subtitle={`${reviews.length} awaiting decision`}>
        {loading && reviews.length === 0 ? (
          <p className="p-4 text-sm text-ink-muted">Loading…</p>
        ) : reviews.length === 0 ? (
          <EmptyState
            title="Queue is clear"
            hint="Invoices routed for approval appear here. Approve, correct the GL account, or reject without leaving the queue."
          />
        ) : (
          <ul className="divide-y divide-border">
            {reviews.map((r) => (
              <li key={r.id} className="flex flex-col gap-1 px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    to={`/runs/${r.run_id}`}
                    className="font-mono text-xs text-ink-subtle underline-offset-2 hover:text-ink hover:underline"
                  >
                    {r.run_id.slice(0, 12)}…
                  </Link>
                  {showTenant ? (
                    <Badge tone="neutral">{tenantShortLabel(r.tenant_id)}</Badge>
                  ) : null}
                  <Badge tone="flag">{r.status}</Badge>
                  {r.required_tier && <Badge tone="neutral">Tier: {r.required_tier}</Badge>}
                </div>
                <p className="text-sm leading-relaxed text-ink">{r.reason}</p>
                <p className="text-xs text-ink-subtle">
                  Requested {new Date(r.requested_at).toLocaleString()} · {r.channel}
                </p>
                <ReviewActions
                  runId={r.run_id}
                  rationale={r.reason}
                  glAccount={r.gl_account}
                  showRationale={false}
                  embedded
                  onResolved={() => void refresh()}
                />
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </PageBody>
  );
}
