import { cn } from "@/lib/cn";

/**
 * The AP Exception Agent brand mark.
 *
 * Concept: a document (the invoice) with a routing "signal" cut through it — an
 * upward node-path that resolves into a checkmark. It reads at once as
 * "paper → decision", the product's whole job: an invoice enters, the agent
 * routes it, a verdict comes out. Drawn on a rounded-square field in the brand
 * teal so it holds up as a favicon, a sidebar mark, and an app-store tile.
 *
 * `currentColor` drives the glyph so the mark inherits ink/brand context; the
 * tile fill is explicit so the mark is legible on any surface.
 */
export function LogoMark({
  className,
  title = "AP Exception Agent",
}: {
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 40 40"
      className={cn("size-8", className)}
      role="img"
      aria-label={title}
    >
      <defs>
        <linearGradient id="ap-mark-tile" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--brand)" />
          <stop
            offset="1"
            stopColor="color-mix(in oklch, var(--brand) 74%, black)"
          />
        </linearGradient>
      </defs>

      {/* tile */}
      <rect
        x="1"
        y="1"
        width="38"
        height="38"
        rx="9"
        fill="url(#ap-mark-tile)"
      />

      {/* invoice sheet, folded corner */}
      <path
        d="M12 9h11l5 5v13a2 2 0 0 1-2 2H12a2 2 0 0 1-2-2V11a2 2 0 0 1 2-2Z"
        fill="var(--brand-contrast)"
        opacity="0.16"
      />
      <path
        d="M23 9l5 5h-5V9Z"
        fill="var(--brand-contrast)"
        opacity="0.34"
      />

      {/* routing signal → checkmark, the "decision" */}
      <path
        d="M14 25.5l4.4 4.4L30 18"
        fill="none"
        stroke="var(--brand-contrast)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* node dots on the path — the pipeline */}
      <circle cx="14" cy="25.5" r="2.1" fill="var(--brand-contrast)" />
      <circle cx="30" cy="18" r="2.1" fill="var(--brand-contrast)" />
    </svg>
  );
}

/**
 * The full lockup: mark + wordmark. Used in the sidebar header. The wordmark is
 * two-weight so "Exception Agent" reads as the qualifier under the product name.
 */
export function Wordmark({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <LogoMark className="size-9 shrink-0" />
      {!compact && (
        <div className="min-w-0 leading-none">
          <div className="truncate text-[15px] font-semibold tracking-tight text-ink">
            AP Exception
          </div>
          <div className="truncate text-[11px] font-medium uppercase tracking-[0.14em] text-brand-ink">
            Agent
          </div>
        </div>
      )}
    </div>
  );
}
