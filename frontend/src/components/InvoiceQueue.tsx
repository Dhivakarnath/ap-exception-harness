import { cn } from "@/lib/cn";
import { tenantShortLabel } from "@/lib/tenants";
import { RouteBadge, StatusBadge } from "@/components/ui/Badge";
import { EmptyState, Panel } from "@/components/ui/Panel";
import type { RunSummary } from "@/types/events";
import type { RunStatus } from "@/store/runState";

/**
 * The invoice queue — the runs this API process has handled, newest first
 * (design §12.4). Two presentations share one data shape:
 *   - `variant="page"`: a full-width run table for the Runs landing page.
 *   - `variant="compact"`: a dense list for a sidebar/rail.
 * Selecting a row hands the run id up so the caller can route to it.
 */
export function InvoiceQueue({
  runs,
  selectedRunId,
  onSelect,
  variant = "compact",
  showTenant = false,
}: {
  runs: RunSummary[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  variant?: "page" | "compact";
  showTenant?: boolean;
}) {
  if (variant === "page") {
    return (
      <RunTable
        runs={runs}
        selectedRunId={selectedRunId}
        onSelect={onSelect}
        showTenant={showTenant}
      />
    );
  }
  return (
    <Panel
      title="Invoice queue"
      subtitle={runs.length > 0 ? `${runs.length} run(s)` : undefined}
    >
      {runs.length === 0 ? (
        <EmptyState
          title="No runs yet"
          hint="Trigger an invoice to see it appear here with its route, cost, and duration."
        />
      ) : (
        <ul className="divide-y divide-border">
          {runs.map((run) => (
            <li key={run.run_id}>
              <button
                type="button"
                onClick={() => onSelect(run.run_id)}
                aria-current={run.run_id === selectedRunId}
                className={cn(
                  "flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors",
                  "hover:bg-surface-2 focus-visible:bg-surface-2",
                  run.run_id === selectedRunId && "bg-brand-soft/40",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium text-ink">
                    {run.vendor}
                  </span>
                  <StatusBadge status={run.status as RunStatus} />
                </div>
                <div className="flex items-center justify-between gap-2 text-xs text-ink-subtle">
                  <span className="font-mono">${run.total}</span>
                  <div className="flex items-center gap-2">
                    {run.route && <RouteBadge route={run.route} />}
                    <span className="font-mono">{formatCost(run.usd)}</span>
                    <span className="font-mono">{duration(run)}</span>
                  </div>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function RunTable({
  runs,
  selectedRunId,
  onSelect,
  showTenant,
}: {
  runs: RunSummary[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  showTenant: boolean;
}) {
  return (
    <Panel
      title="Run history"
      subtitle={runs.length > 0 ? `${runs.length} run(s), newest first` : undefined}
    >
      {runs.length === 0 ? (
        <EmptyState
          title="No runs yet"
          hint="Trigger an invoice above to see it appear here with its route, cost, and duration."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs font-medium uppercase tracking-wide text-ink-subtle">
                <th scope="col" className="px-4 py-2.5 font-medium">Invoice</th>
                {showTenant ? (
                  <th scope="col" className="px-4 py-2.5 font-medium">Tenant</th>
                ) : null}
                <th scope="col" className="px-4 py-2.5 font-medium">Vendor</th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">Total</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Route</th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">Cost</th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">Duration</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {runs.map((run) => (
                <tr
                  key={run.run_id}
                  onClick={() => onSelect(run.run_id)}
                  aria-current={run.run_id === selectedRunId}
                  tabIndex={0}
                  role="link"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(run.run_id);
                    }
                  }}
                  className={cn(
                    "cursor-pointer transition-colors hover:bg-surface-2 focus-visible:bg-surface-2",
                    run.run_id === selectedRunId && "bg-brand-soft/40",
                  )}
                >
                  <td className="px-4 py-3">
                    <span className="font-mono text-xs text-ink-muted">
                      {run.invoice_id}
                    </span>
                  </td>
                  {showTenant ? (
                    <td className="px-4 py-3 text-xs text-ink-subtle">
                      {tenantShortLabel(run.tenant_id)}
                    </td>
                  ) : null}
                  <td className="px-4 py-3 font-medium text-ink">{run.vendor}</td>
                  <td className="px-4 py-3 text-right font-mono text-ink">
                    ${run.total}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={run.status as RunStatus} />
                  </td>
                  <td className="px-4 py-3">
                    {run.route ? <RouteBadge route={run.route} /> : (
                      <span className="text-ink-subtle">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-ink-muted">
                    {formatCost(run.usd)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-ink-muted">
                    {duration(run)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(5)}`;
  return `$${usd.toFixed(4)}`;
}

function duration(run: RunSummary): string {
  if (!run.finished_at) return "—";
  const ms =
    new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
