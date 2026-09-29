import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * The standard header every page opens with: an optional eyebrow (breadcrumb /
 * section label), a title, a one-line description that states what the page is
 * for, and a trailing actions slot. Consistent typographic rhythm across pages
 * is what makes the set read as one product rather than five screens.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  className,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-start justify-between gap-x-6 gap-y-3",
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow && (
          <div className="mb-1 text-[11px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            {eyebrow}
          </div>
        )}
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          {title}
        </h1>
        {description && (
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/**
 * A scrollable page body with the standard max-width and padding rhythm. Pages
 * wrap their content in this so spacing is identical everywhere.
 */
export function PageBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className={cn("mx-auto w-full max-w-6xl px-6 py-6", className)}>
        {children}
      </div>
    </div>
  );
}
