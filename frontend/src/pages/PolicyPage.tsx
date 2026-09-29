import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { PageState } from "@/components/layout/PageState";
import { Panel } from "@/components/ui/Panel";
import { Badge } from "@/components/ui/Badge";
import { useAsync } from "@/lib/useAsync";
import {
  ALL_TENANTS,
  DEMO_TENANTS,
  tenantLabel,
  type DemoTenantId,
} from "@/lib/tenants";
import { useTenantFilter } from "@/store/useTenantFilter";
import { fetchPolicy } from "@/transport/api";
import type { PolicyPack } from "@/types/events";

/**
 * Policy page — the active policy pack, rendered exactly as the agent applies
 * it. Every threshold, tolerance, and delegation tier is real config from
 * GET /policy, not prose about how it "should" work. This is the governance
 * artefact an AP lead audits before trusting the automation.
 */
export function PolicyPage() {
  const { tenant } = useTenantFilter();
  const tenantIds: DemoTenantId[] =
    tenant === ALL_TENANTS ? DEMO_TENANTS.map((t) => t.id) : [tenant];
  const state = useAsync(
    () => Promise.all(tenantIds.map((id) => fetchPolicy(id))),
    tenantIds.join(","),
  );

  return (
    <PageBody className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Governance"
        title="Policy pack"
        description="Active YAML policy packs rendered exactly as the agent applies them. Scope follows the View tenant selector in the sidebar."
      />
      <PageState state={state}>
        {(packs) => (
          <div className="flex flex-col gap-10">
            {packs.map((pack, index) => (
              <PolicyPackView
                key={tenantIds[index]}
                tenantId={tenantIds[index]}
                pack={pack}
                showTenantHeading={tenant === ALL_TENANTS}
              />
            ))}
          </div>
        )}
      </PageState>
    </PageBody>
  );
}

function PolicyPackView({
  tenantId,
  pack,
  showTenantHeading,
}: {
  tenantId: DemoTenantId;
  pack: PolicyPack;
  showTenantHeading: boolean;
}) {
  return (
    <section className="flex flex-col gap-6">
      {showTenantHeading ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <h2 className="text-lg font-semibold text-ink">{tenantLabel(tenantId)}</h2>
          <Badge tone="brand" className="font-mono">{pack.identity}</Badge>
        </div>
      ) : (
        <div className="flex justify-end">
          <Badge tone="brand" className="font-mono">{pack.identity}</Badge>
        </div>
      )}
      <p className="max-w-3xl text-sm leading-relaxed text-ink-muted">
        {pack.description.trim()}
      </p>
      <Thresholds pack={pack} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Tolerances pack={pack} />
        <Duplicates pack={pack} />
      </div>
      <DoaMatrix pack={pack} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Sod pack={pack} />
        <PaymentTerms pack={pack} />
      </div>
    </section>
  );
}

function StatGrid({ children }: { children: React.ReactNode }) {
  return (
    <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-3 lg:grid-cols-4">
      {children}
    </dl>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1 bg-surface p-4">
      <dt className="text-xs text-ink-subtle">{label}</dt>
      <dd className="font-mono text-lg font-semibold text-ink">{value}</dd>
      {hint && <p className="text-[11px] leading-snug text-ink-subtle">{hint}</p>}
    </div>
  );
}

function Thresholds({ pack }: { pack: PolicyPack }) {
  const t = pack.thresholds;
  return (
    <Panel
      title="Decision thresholds"
      subtitle="The gates that decide touchless vs. escalation"
    >
      <StatGrid>
        <Stat
          label="Touchless ceiling"
          value={`$${t.touchless_max}`}
          hint="Above this, a human approver is always required."
        />
        <Stat
          label="Min confidence · touchless"
          value={`${(t.min_confidence_for_touchless * 100).toFixed(0)}%`}
          hint="Extraction confidence needed to auto-approve."
        />
        <Stat
          label="Min confidence · GL coding"
          value={`${(t.min_confidence_for_gl_coding * 100).toFixed(0)}%`}
          hint="Below this, coding is routed for review."
        />
        <Stat
          label="Threshold-avoidance band"
          value={`${t.threshold_avoidance_band_pct}%`}
          hint="Amounts just under a DOA boundary are flagged."
        />
        <Stat
          label="New-vendor window"
          value={`${t.new_vendor_days} days`}
          hint="Vendors newer than this are treated as new."
        />
      </StatGrid>
    </Panel>
  );
}

function Tolerances({ pack }: { pack: PolicyPack }) {
  const t = pack.tolerances;
  return (
    <Panel title="Matching tolerances" subtitle="How far a line may drift and still pass">
      <dl className="divide-y divide-border">
        <Row label="Unit-price variance" value={`± ${t.price_pct}%`} />
        <Row label="Quantity variance" value={`± ${t.quantity_pct}%`} />
        <Row label="Total absolute" value={`$${t.total_absolute}`} />
        <Row
          label="Partial delivery"
          value={<BoolBadge on={t.allow_partial_delivery} />}
        />
        <Row label="Overbilling" value={<BoolBadge on={t.allow_overbilling} />} />
      </dl>
    </Panel>
  );
}

function Duplicates({ pack }: { pack: PolicyPack }) {
  const d = pack.duplicates;
  return (
    <Panel title="Duplicate detection" subtitle="Exact and fuzzy near-miss matching">
      <dl className="divide-y divide-border">
        <Row label="Lookback window" value={`${d.lookback_days} days`} />
        <Row
          label="Fuzzy number similarity"
          value={`≥ ${(d.fuzzy_number_similarity * 100).toFixed(0)}%`}
        />
        <Row label="Fuzzy amount tolerance" value={`$${d.fuzzy_amount_tolerance}`} />
        <Row label="Fuzzy date window" value={`± ${d.fuzzy_date_window_days} days`} />
      </dl>
    </Panel>
  );
}

function DoaMatrix({ pack }: { pack: PolicyPack }) {
  return (
    <Panel
      title="Delegation of authority"
      subtitle="Who may approve, by amount — the escalation ladder"
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs font-medium uppercase tracking-wide text-ink-subtle">
              <th scope="col" className="px-4 py-2.5 font-medium">Tier</th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">
                Up to
              </th>
              <th scope="col" className="px-4 py-2.5 font-medium">Roles</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {pack.doa.bands.map((band) => (
              <tr key={band.tier}>
                <td className="px-4 py-3 font-medium capitalize text-ink">
                  {band.tier.replace(/_/g, " ")}
                </td>
                <td className="px-4 py-3 text-right font-mono text-ink">
                  {band.max_amount ? `$${band.max_amount}` : "No ceiling"}
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1.5">
                    {band.roles.map((r) => (
                      <span
                        key={r}
                        className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-ink-muted"
                      >
                        {r}
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

const SOD_LABEL: Record<string, string> = {
  agent_may_code: "Agent may code the GL account",
  agent_may_route: "Agent may route the decision",
  agent_may_approve_below_touchless: "Agent may approve below the touchless ceiling",
  agent_may_approve_above_touchless: "Agent may approve above the touchless ceiling",
  approver_must_differ_from_coder: "Approver must differ from the coder",
  approver_must_differ_from_payer: "Approver must differ from the payer",
};

function Sod({ pack }: { pack: PolicyPack }) {
  return (
    <Panel
      title="Segregation of duties"
      subtitle="What the agent may do on its own authority"
    >
      <dl className="divide-y divide-border">
        {Object.entries(pack.sod).map(([key, on]) => (
          <Row key={key} label={SOD_LABEL[key] ?? key} value={<BoolBadge on={on} />} />
        ))}
      </dl>
    </Panel>
  );
}

function PaymentTerms({ pack }: { pack: PolicyPack }) {
  const p = pack.payment_terms;
  return (
    <Panel title="Payment terms" subtitle="Defaults and early-discount capture">
      <dl className="divide-y divide-border">
        <Row label="Default terms" value={<span className="font-mono">{p.default_terms}</span>} />
        <Row label="Capture discounts" value={<BoolBadge on={p.capture_discounts} />} />
        <Row
          label="Min annualised discount to flag"
          value={`${p.min_discount_annualised_pct}%`}
        />
        <Row label="Requires goods receipt" value={<BoolBadge on={pack.require_grn} />} />
        <Row
          label="Non-PO invoices allowed"
          value={<BoolBadge on={pack.allow_non_po_invoices} />}
        />
      </dl>
    </Panel>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2.5">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="shrink-0 font-mono text-sm text-ink">{value}</dd>
    </div>
  );
}

function BoolBadge({ on }: { on: boolean }) {
  return (
    <Badge tone={on ? "pass" : "neutral"}>{on ? "Allowed" : "Not allowed"}</Badge>
  );
}
