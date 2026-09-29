import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * A titled surface — the primary container for a view. Not a decorative card:
 * one level only (nested panels are a design smell), a clear header with an
 * optional trailing slot for counts/actions, and a body that scrolls
 * independently when the run is long.
 */
export function Panel({
  title,
  subtitle,
  trailing,
  children,
  className,
  bodyClassName,
  as: Tag = "section",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  as?: "section" | "aside" | "div";
}) {
  return (
    <Tag
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-sm",
        className,
      )}
    >
      {(title || trailing) && (
        <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            {title && (
              <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
            )}
            {subtitle && (
              <p className="truncate text-xs text-ink-subtle">{subtitle}</p>
            )}
          </div>
          {trailing && <div className="shrink-0">{trailing}</div>}
        </header>
      )}
      <div className={cn("min-h-0 flex-1 overflow-auto", bodyClassName)}>
        {children}
      </div>
    </Tag>
  );
}

/** A quiet empty state — never a spinner that hides a dead run. */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full min-h-32 flex-col items-center justify-center gap-2 p-6 text-center">
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {hint && <p className="max-w-xs text-xs text-ink-subtle">{hint}</p>}
      {action}
    </div>
  );
}
