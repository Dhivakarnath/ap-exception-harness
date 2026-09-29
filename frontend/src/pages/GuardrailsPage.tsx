import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { PageState } from "@/components/layout/PageState";
import { Panel } from "@/components/ui/Panel";
import { Badge } from "@/components/ui/Badge";
import { useAsync } from "@/lib/useAsync";
import { fetchGuardrails } from "@/transport/api";
import type { GuardrailLayer, GuardrailsConfig } from "@/types/events";

/**
 * Guardrails page — the three-layer defense, from real config. Each layer says
 * where it runs, what it enforces, and a concrete example of what it refuses.
 * The scope-token matrix shows least privilege in effect; the closing note
 * states the hard architectural boundary: there is no payment capability.
 */
export function GuardrailsPage() {
  const state = useAsync(fetchGuardrails, "guardrails");

  return (
    <PageBody className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Security"
        title="Guardrails"
        description="Defense in depth: three independent layers, each enforcing in code — not prompt text — so a failure or jailbreak at one layer is still caught by the next."
      />
      <PageState state={state}>
        {(config) => (
          <>
            <ol className="flex flex-col gap-4">
              {config.layers.map((layer) => (
                <LayerCard key={layer.id} layer={layer} />
              ))}
            </ol>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.4fr_1fr]">
              <TokenMatrix tokens={config.tokens} />
              <NoPayment config={config} />
            </div>
          </>
        )}
      </PageState>
    </PageBody>
  );
}

function LayerCard({ layer }: { layer: GuardrailLayer }) {
  return (
    <li className="rounded-lg border border-border bg-surface shadow-sm">
      <div className="flex items-start gap-4 border-b border-border p-4">
        <div
          className="grid size-9 shrink-0 place-items-center rounded-md bg-brand-soft font-mono text-sm font-semibold text-brand-ink"
          aria-hidden
        >
          L{layer.layer}
        </div>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-ink">{layer.name}</h2>
          <p className="mt-0.5 text-xs text-ink-subtle">{layer.where}</p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-[1fr_1fr]">
        <div>
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
            Enforces
          </p>
          <p className="text-sm leading-relaxed text-ink-muted">{layer.enforces}</p>
        </div>
        <div>
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
            Blocks, for example
          </p>
          <p className="rounded-md bg-fail-soft/50 px-3 py-2 text-sm leading-relaxed text-fail-ink">
            {layer.blocks_example}
          </p>
        </div>
      </div>
    </li>
  );
}

function TokenMatrix({ tokens }: { tokens: Record<string, string[]> }) {
  const entries = Object.entries(tokens);
  return (
    <Panel
      title="Purpose-scoped tokens"
      subtitle="Least privilege — each token carries only the scopes its job needs"
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs font-medium uppercase tracking-wide text-ink-subtle">
              <th scope="col" className="px-4 py-2.5 font-medium">Token</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Granted scopes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {entries.map(([token, scopes]) => (
              <tr key={token}>
                <td className="px-4 py-3 font-mono text-xs text-ink">{token}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1.5">
                    {scopes.map((s) => (
                      <span
                        key={s}
                        className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-ink-muted"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function NoPayment({ config }: { config: GuardrailsConfig }) {
  return (
    <Panel title="Hard boundary">
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-center gap-2">
          <Badge tone={config.no_payment_capability ? "pass" : "fail"}>
            {config.no_payment_capability
              ? "No payment capability"
              : "Payment capability present"}
          </Badge>
        </div>
        <p className="text-sm leading-relaxed text-ink-muted">{config.note}</p>
      </div>
    </Panel>
  );
}
