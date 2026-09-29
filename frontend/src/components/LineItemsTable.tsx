import { cn } from "@/lib/cn";
import { EmptyState, Panel } from "@/components/ui/Panel";
import type { ExtractionLineItem } from "@/types/events";

/**
 * The extracted line table (design §12.2). Shows every line the model read —
 * description, quantity + unit, unit price, line total — with the line's
 * confidence. A line that carries a source region is selectable and drives the
 * document highlight-back, exactly like the header fields, so a reviewer can see
 * where on the page each row came from (FR-2.5).
 */
export function LineItemsTable({
  lineItems,
  selectedRef,
  onSelect,
}: {
  lineItems: ExtractionLineItem[];
  selectedRef: string | null;
  onSelect: (regionRef: string | null) => void;
}) {
  return (
    <Panel
      title="Line items"
      subtitle={
        lineItems.length > 0
          ? `${lineItems.length} line(s) recovered from the document`
          : "The invoice's line table, as extracted"
      }
    >
      {lineItems.length === 0 ? (
        <EmptyState
          title="No line items"
          hint="Extracted invoice lines appear here on a live model run. A header-only service invoice has none."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs font-medium uppercase tracking-wide text-ink-subtle">
                <th scope="col" className="px-3 py-2 font-medium">
                  Description
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Qty
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Unit price
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Line total
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Conf.
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {lineItems.map((line) => {
                const selectable = Boolean(line.region_ref);
                const selected = selectable && line.region_ref === selectedRef;
                const qty = line.unit_of_measure
                  ? `${line.quantity} ${line.unit_of_measure}`
                  : line.quantity;
                return (
                  <tr
                    key={line.line_number}
                    onClick={
                      selectable
                        ? () =>
                            onSelect(
                              selected ? null : (line.region_ref ?? null),
                            )
                        : undefined
                    }
                    aria-current={selected}
                    tabIndex={selectable ? 0 : undefined}
                    role={selectable ? "button" : undefined}
                    onKeyDown={
                      selectable
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onSelect(
                                selected ? null : (line.region_ref ?? null),
                              );
                            }
                          }
                        : undefined
                    }
                    className={cn(
                      "transition-colors",
                      selectable
                        ? "cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2"
                        : "cursor-default",
                      selected && "bg-brand-soft/40",
                    )}
                  >
                    <td className="px-3 py-2.5 text-ink">
                      <span className="block max-w-[22ch] truncate" title={line.description}>
                        {line.description}
                      </span>
                      {line.sku && (
                        <span className="font-mono text-[11px] text-ink-subtle">
                          SKU {line.sku}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-ink-muted">
                      {qty}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-ink-muted">
                      {line.unit_price}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-ink">
                      {line.line_total}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-[11px] text-ink-subtle">
                      {line.confidence != null
                        ? `${Math.round(line.confidence * 100)}%`
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
