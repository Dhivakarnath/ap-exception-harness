import { useRef } from "react";
import { cn } from "@/lib/cn";

/**
 * An accessible tab strip (WAI-ARIA tabs pattern): `role="tablist"` with
 * `role="tab"` children, `aria-selected`, roving focus, and Left/Right/Home/End
 * keyboard navigation. Purely presentational selection — the parent owns the
 * active id and renders the panel — so it composes with any view switch.
 */
export interface TabItem<T extends string> {
  id: T;
  label: string;
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKeyDown = (e: React.KeyboardEvent) => {
    const idx = items.findIndex((t) => t.id === value);
    let next = idx;
    if (e.key === "ArrowRight") next = (idx + 1) % items.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    else return;
    e.preventDefault();
    const target = items[next];
    if (target) {
      onChange(target.id);
      refs.current[target.id]?.focus();
    }
  };

  return (
    <div role="tablist" aria-label={label} className="flex gap-1" onKeyDown={onKeyDown}>
      {items.map((t) => {
        const selected = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            role="tab"
            type="button"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              selected
                ? "bg-brand-soft text-brand-ink"
                : "text-ink-subtle hover:bg-surface-2 hover:text-ink",
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
