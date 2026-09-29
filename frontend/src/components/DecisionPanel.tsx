import { Badge, RouteBadge } from "@/components/ui/Badge";
import { EmptyState, Panel } from "@/components/ui/Panel";
import type { CostPayload, DecisionPayload } from "@/types/events";

const ACTOR_LABEL: Record<string, string> = {
  agent: "Agent (coder/router)",
  human: "Human approver",
  policy_engine: "Policy engine",
};

/**
 * The terminal decision (design §12.2): route, rationale, GL coding, the cited
 * clauses that justify it, who decided, and the run's cost. This is the
 * money-affecting output, so it reads as a record, not a summary — every claim
 * (the code, the authority, the cost) is shown with its provenance.
 */
export function DecisionPanel({
  decision,
  cost,
}: {
  decision: DecisionPayload | null;
  cost: CostPayload | null;
}) {
  return (
    <Panel title="Decision" subtitle="Route, coding, authority, and cost">
      {!decision ? (
        <EmptyState title="No decision yet" hint="The terminal route appears once the run reaches the decide node." />
      ) : (
        <div className="flex flex-col gap-4 p-4">
          <div className="flex items-center gap-2">
            <RouteBadge route={decision.route} />
            <span className="text-xs text-ink-subtle">
              by {ACTOR_LABEL[decision.actor] ?? decision.actor}
            </span>
          </div>

          <div>
            <p className="text-xs text-ink-subtle">Rationale</p>
            <p className="mt-1 text-sm leading-relaxed text-ink">
              {decision.rationale}
            </p>
          </div>

          {decision.gl_account && (
            <div className="flex items-center gap-2">
              <p className="text-xs text-ink-subtle">GL account</p>
              <span className="font-mono text-sm text-ink">
                {decision.gl_account}
              </span>
            </div>
          )}

          {decision.citations && decision.citations.length > 0 && (
            <div>
              <p className="text-xs text-ink-subtle">
                Grounded in {decision.citations.length} clause
                {decision.citations.length === 1 ? "" : "s"}
              </p>
              <ul className="mt-1 flex flex-wrap gap-1">
                {decision.citations.map((c) => (
                  <li key={c}>
                    <Badge tone="brand" className="font-mono text-[11px]">
                      {c}
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {cost && (
            <div className="border-t border-border pt-3">
              <p className="text-xs text-ink-subtle">Cost</p>
              <div className="mt-1 flex items-center gap-4 text-sm">
                <span className="font-mono text-ink">
                  {formatUsd(cost.usd)}
                </span>
                <span className="text-xs text-ink-subtle">
                  {cost.input_tokens.toLocaleString()} in ·{" "}
                  {cost.output_tokens.toLocaleString()} out tokens
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function formatUsd(usd: number): string {
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(6)}`;
  return `$${usd.toFixed(4)}`;
}
