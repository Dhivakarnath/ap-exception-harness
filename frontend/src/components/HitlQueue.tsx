import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { EmptyState, Panel } from "@/components/ui/Panel";
import { Badge } from "@/components/ui/Badge";
import { submitReview } from "@/transport/api";
import type { RunState } from "@/store/runState";

/**
 * The HITL review queue (design §12.5). When a run routes for approval, this
 * shows the case — the decision rationale, the proposed GL coding, and the
 * cited clauses — and offers the reviewer approve / edit / reject.
 *
 * Submitting a decision POSTs to /runs/{id}/review, which resumes the paused
 * graph on the same in-process Supervisor (its durable checkpointer holds the
 * pause) and drives the run to a terminal decision. The action is real: the run
 * actually continues on the human's call, with the human recorded as approver.
 */
export function HitlQueue({
  state,
  runId,
  onResolved,
}: {
  state: RunState;
  runId: string | null;
  onResolved?: () => void;
}) {
  const pending = state.status === "awaiting_review";
  const decision = state.decision;

  return (
    <Panel title="Human review" subtitle="Approve, edit, or reject escalations">
      {!pending || !decision || !runId ? (
        <EmptyState
          title="Nothing awaiting review"
          hint="Runs that route for approval appear here with the full case and the reviewer's actions."
        />
      ) : (
        <ReviewActions
          runId={runId}
          rationale={decision.rationale}
          glAccount={decision.gl_account ?? null}
          citations={decision.citations ?? []}
          onResolved={onResolved}
        />
      )}
    </Panel>
  );
}

type Phase = "idle" | "submitting" | "done" | "error";

export function ReviewActions({
  runId,
  rationale,
  glAccount,
  citations = [],
  showRationale = true,
  embedded = false,
  onResolved,
}: {
  runId: string;
  rationale: string;
  glAccount: string | null;
  citations?: string[];
  showRationale?: boolean;
  embedded?: boolean;
  onResolved: (() => void) | undefined;
}) {
  const [editing, setEditing] = useState(false);
  const [editedGl, setEditedGl] = useState(glAccount ?? "");
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const resolve = async (decision: "approve" | "edit" | "reject") => {
    setPhase("submitting");
    setMessage(null);
    try {
      const res = await submitReview(runId, {
        decision,
        approver_identity: "reviewer@retail-demo",
        ...(decision === "edit" ? { edited_gl_account: editedGl } : {}),
      });
      setPhase("done");
      setMessage(`Resumed — final route: ${res.route ?? "unknown"}`);
      onResolved?.();
    } catch (err) {
      setPhase("error");
      setMessage(err instanceof Error ? err.message : "Review failed");
    }
  };

  const busy = phase === "submitting";
  const resolved = phase === "done";

  return (
    <div className={embedded ? "flex flex-col gap-3 pt-2" : "flex flex-col gap-4 p-4"}>
      {showRationale ? (
        <div>
          <p className="text-xs text-ink-subtle">Rationale</p>
          <p className="mt-1 text-sm leading-relaxed text-ink">{rationale}</p>
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <p className="text-xs text-ink-subtle">Proposed GL</p>
        {editing && !resolved ? (
          <input
            value={editedGl}
            onChange={(e) => setEditedGl(e.target.value)}
            className="w-28 rounded-md border border-border-strong bg-surface-2 px-2 py-1 font-mono text-sm text-ink"
            aria-label="Edited GL account"
          />
        ) : (
          <span className="font-mono text-sm text-ink">{glAccount ?? "—"}</span>
        )}
      </div>

      {citations.length > 0 && (
        <div>
          <p className="text-xs text-ink-subtle">Cited clauses</p>
          <ul className="mt-1 flex flex-col gap-1">
            {citations.map((c) => (
              <li key={c}>
                <Badge tone="brand" className="font-mono">
                  {c}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      )}

      {message && (
        <p
          role={phase === "error" ? "alert" : "status"}
          className={
            phase === "error"
              ? "rounded-md bg-fail-soft px-3 py-2 text-xs text-fail-ink"
              : "rounded-md bg-pass-soft px-3 py-2 text-xs text-pass-ink"
          }
        >
          {message}
        </p>
      )}

      {!resolved && (
        <div className="flex flex-wrap gap-2 border-t border-border pt-3">
          <Button disabled={busy} onClick={() => void resolve("approve")}>
            Approve
          </Button>
          {editing ? (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => void resolve("edit")}
            >
              Save &amp; approve
            </Button>
          ) : (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              Edit GL
            </Button>
          )}
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => void resolve("reject")}
          >
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}
