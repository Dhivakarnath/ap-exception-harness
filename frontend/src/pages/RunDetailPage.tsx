import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CheckLedger } from "@/components/CheckLedger";
import { DecisionPanel } from "@/components/DecisionPanel";
import { DocumentViewer } from "@/components/DocumentViewer";
import { ErrorPanel } from "@/components/ErrorPanel";
import { ExtractionPanel } from "@/components/ExtractionPanel";
import { ExtractionProcess } from "@/components/ExtractionProcess";
import { LineItemsTable } from "@/components/LineItemsTable";
import { HitlQueue } from "@/components/HitlQueue";
import { PipelineFlow } from "@/components/PipelineFlow";
import { StatusBadge } from "@/components/ui/Badge";
import { Tabs } from "@/components/ui/Tabs";
import { BackIcon } from "@/components/ui/icons";
import { PageBody } from "@/components/layout/PageHeader";
import { documentImageUrl, documentImageUrlForRun, fetchRun } from "@/transport/api";
import { useRunStream } from "@/store/useRunStream";
import type { RunSummary } from "@/types/events";

type Tab = "pipeline" | "extraction" | "decision";

const TABS: { id: Tab; label: string }[] = [
  { id: "pipeline", label: "Pipeline & checks" },
  { id: "extraction", label: "Extraction & source" },
  { id: "decision", label: "Decision" },
];

/**
 * The run-detail route (/runs/:id). Subscribes to the run's live event stream
 * and renders the folded state — pipeline, streaming check ledger, extraction
 * with document highlight-back, the decision, and the HITL review rail that
 * actually resumes the run. A completed run replays identically to a live one
 * (the single-envelope design), so this page serves both from one store.
 */
export function RunDetailPage() {
  const { runId = null } = useParams();
  const [tab, setTab] = useState<Tab>("pipeline");
  const [selectedRegion, setSelectedRegion] = useState<string | null>(null);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const { state, connection } = useRunStream(runId, refreshNonce);
  const live = connection === "open" || connection === "connecting";

  const loadSummary = useCallback(async () => {
    if (!runId) return;
    try {
      setSummary(await fetchRun(runId));
    } catch {
      /* the header still works from the streamed state */
    }
  }, [runId]);

  const onReviewResolved = useCallback(() => {
    void loadSummary();
    setRefreshNonce((n) => n + 1);
  }, [loadSummary]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    if (state.status === "completed" || state.status === "failed") {
      void loadSummary();
    }
  }, [state.status, loadSummary]);

  const documentImage =
    summary?.scenario === "upload" && runId
      ? documentImageUrlForRun(runId)
      : summary?.document_stem
        ? documentImageUrl(summary.document_stem)
        : null;

  const showConnectingBanner =
    live && state.stages.length === 0 && state.checks.length === 0;

  return (
    <PageBody className="flex min-h-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            to="/"
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm text-ink-muted transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <BackIcon className="size-4" />
            Runs
          </Link>
          <span aria-hidden className="text-ink-subtle">/</span>
          <span className="font-mono text-sm text-ink">
            {runId ? `run ${runId.slice(0, 8)}` : "no run"}
          </span>
          {summary?.vendor && (
            <span className="hidden text-sm text-ink-muted sm:inline">
              {summary.vendor} · ${summary.total}
            </span>
          )}
          {runId && <StatusBadge status={state.status} />}
          {summary?.trace_url && (
            <a
              href={summary.trace_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs font-medium text-brand-ink transition-colors hover:bg-surface-2"
            >
              Deep trace ↗
            </a>
          )}
        </div>
        <Tabs items={TABS} value={tab} onChange={setTab} label="Run views" />
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-4" role="tabpanel">
        {tab === "pipeline" && (
          <>
            {showConnectingBanner ? (
              <div
                className="rounded-lg border border-brand/30 bg-brand-soft/30 px-4 py-3 text-sm text-brand-ink"
                role="status"
              >
                Connecting to the live run stream… pipeline stages and checks will
                appear momentarily.
              </div>
            ) : null}

            <PipelineFlow
              stages={state.stages}
              status={state.status}
              checkCount={state.checks.length}
              hasDecision={state.decision != null}
            />

            <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]">
              <div className="flex min-h-0 min-w-0 flex-col gap-4">
                <CheckLedger checks={state.checks} live={live} />
                <ErrorPanel errors={state.errors} traceId={state.traceId} />
              </div>
              <aside className="min-h-0 lg:sticky lg:top-4 lg:self-start">
                <HitlQueue state={state} runId={runId} onResolved={onReviewResolved} />
              </aside>
            </div>
          </>
        )}

        {tab === "extraction" && (
          <div className="grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]">
            <div className="grid min-h-0 grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="flex min-h-0 flex-col gap-4">
                <ExtractionPanel
                  fields={state.extraction}
                  selectedRef={selectedRegion}
                  onSelect={setSelectedRegion}
                />
                <LineItemsTable
                  lineItems={state.lineItems}
                  selectedRef={selectedRegion}
                  onSelect={setSelectedRegion}
                />
                <ExtractionProcess meta={state.extractionMeta} />
              </div>
              <DocumentViewer
                fields={state.extraction}
                selectedRef={selectedRegion}
                onSelect={setSelectedRegion}
                imageUrl={documentImage}
              />
            </div>
            <aside className="min-h-0 xl:sticky xl:top-4 xl:self-start">
              <HitlQueue state={state} runId={runId} onResolved={onReviewResolved} />
            </aside>
          </div>
        )}

        {tab === "decision" && (
          <div className="grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]">
            <DecisionPanel decision={state.decision} cost={state.cost} />
            <aside className="min-h-0 xl:sticky xl:top-4 xl:self-start">
              <HitlQueue state={state} runId={runId} onResolved={onReviewResolved} />
            </aside>
          </div>
        )}
      </div>
    </PageBody>
  );
}
