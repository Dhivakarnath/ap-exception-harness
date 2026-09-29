import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { InvoiceQueue } from "@/components/InvoiceQueue";
import { Toolbar } from "@/components/Toolbar";
import { KpiStrip } from "@/components/KpiStrip";
import { ALL_TENANTS } from "@/lib/tenants";
import { useTenantFilter } from "@/store/useTenantFilter";
import { notifyOperationsRefresh } from "@/lib/operationsRefresh";
import { listRuns } from "@/transport/api";
import type { RunSummary } from "@/types/events";

/**
 * The Runs page — the platform's landing surface. An upload toolbar, a summary
 * KPI strip, and the queue of runs this API process has handled. Selecting a run
 * navigates to its dedicated detail route, where the live pipeline streams.
 */
export function RunsPage() {
  const navigate = useNavigate();
  const { tenant } = useTenantFilter();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [banner, setBanner] = useState<string | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const next = await listRuns(tenant);
      setRuns((prev) => {
        const changed =
          next.length !== prev.length ||
          next.some((r, i) => r.run_id !== prev[i]?.run_id || r.status !== prev[i]?.status);
        if (changed) notifyOperationsRefresh();
        return next;
      });
    } catch {
      /* best-effort: the queue simply shows what it last had */
    }
  }, [tenant]);

  useEffect(() => {
    void refreshRuns();
    const id = window.setInterval(() => void refreshRuns(), 2000);
    return () => window.clearInterval(id);
  }, [refreshRuns]);

  const onStarted = useCallback(
    (runId: string) => {
      setBanner(null);
      navigate(`/runs/${runId}`);
    },
    [navigate],
  );

  return (
    <>
      <PageBody className="flex flex-col gap-6">
        <PageHeader
          eyebrow="Accounts Payable"
          title="Invoice runs"
          description="Upload an invoice and watch it move through extraction, the deterministic policy checks, and the routing decision — every step streamed and attributable."
          actions={<Toolbar onStarted={onStarted} onError={setBanner} />}
        />

        {banner && (
          <div
            role="alert"
            className="rounded-lg border border-fail/40 bg-fail-soft px-4 py-2.5 text-sm text-fail-ink"
          >
            {banner}
          </div>
        )}

        <div className="flex flex-col gap-2">
          <KpiStrip tenantId={tenant} />
          <Link
            to="/operations"
            className="self-end text-xs font-medium text-brand-ink underline-offset-2 hover:underline"
          >
            Full operations overview →
          </Link>
        </div>

        <InvoiceQueue
          runs={runs}
          selectedRunId={null}
          onSelect={(id) => navigate(`/runs/${id}`)}
          variant="page"
          showTenant={tenant === ALL_TENANTS}
        />
      </PageBody>
    </>
  );
}
