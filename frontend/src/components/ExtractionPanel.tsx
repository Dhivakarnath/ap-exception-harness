import { cn } from "@/lib/cn";
import { EmptyState, Panel } from "@/components/ui/Panel";
import type { ExtractionField } from "@/types/events";

const FIELD_LABEL: Record<string, string> = {
  invoice_number: "Invoice #",
  invoice_date: "Invoice date",
  vendor_name: "Vendor",
  currency: "Currency",
  subtotal: "Subtotal",
  total_amount: "Total",
  po_reference: "PO reference",
};

/**
 * Extracted fields with confidence (design §12.2). Each field is selectable;
 * selecting one drives the document highlight-back so a reviewer can see exactly
 * where on the source the value came from (FR-2.5). Confidence is shown as a bar
 * so a low-confidence field is visible at a glance, not buried in a number.
 */
export function ExtractionPanel({
  fields,
  selectedRef,
  onSelect,
}: {
  fields: ExtractionField[];
  selectedRef: string | null;
  onSelect: (regionRef: string | null) => void;
}) {
  return (
    <Panel title="Extraction" subtitle="Fields recovered from the document, with confidence">
      {fields.length === 0 ? (
        <EmptyState
          title="No fields yet"
          hint="Extracted invoice fields appear here on a live model run, each linked to its spot on the page."
        />
      ) : (
        <ul className="divide-y divide-border">
          {fields.map((field) => {
            const selectable = Boolean(field.region_ref);
            const selected =
              selectable && field.region_ref === selectedRef;
            return (
              <li key={field.name}>
                <button
                  type="button"
                  disabled={!selectable}
                  onClick={() =>
                    onSelect(selected ? null : (field.region_ref ?? null))
                  }
                  className={cn(
                    "flex w-full flex-col gap-1.5 px-4 py-2.5 text-left transition-colors",
                    selectable
                      ? "hover:bg-surface-2 focus-visible:bg-surface-2"
                      : "cursor-default",
                    selected && "bg-brand-soft/40",
                  )}
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-xs text-ink-subtle">
                      {FIELD_LABEL[field.name] ?? field.name}
                    </span>
                    <span className="truncate font-mono text-sm text-ink">
                      {field.value ?? "—"}
                    </span>
                  </div>
                  {field.confidence != null && (
                    <ConfidenceBar value={field.confidence} />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  // High confidence reads as brand/pass; a dip toward uncertainty shifts to flag.
  const tone =
    value >= 0.9 ? "bg-pass" : value >= 0.7 ? "bg-flag" : "bg-fail";
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1 flex-1 overflow-hidden rounded-full bg-surface-3"
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Extraction confidence"
      >
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right font-mono text-[11px] text-ink-subtle">
        {pct}%
      </span>
    </div>
  );
}
