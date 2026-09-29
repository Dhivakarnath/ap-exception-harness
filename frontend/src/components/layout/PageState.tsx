import type { ReactNode } from "react";
import type { AsyncState } from "@/lib/useAsync";

/**
 * Standard loading / error / ready rendering for a page that loads one
 * endpoint. Loading shows a quiet skeleton; an error is shown loudly (never
 * swallowed into an empty page); ready hands the non-null data to the child.
 */
export function PageState<T>({
  state,
  children,
}: {
  state: AsyncState<T>;
  children: (data: T) => ReactNode;
}) {
  if (state.loading) {
    return (
      <div className="flex flex-col gap-3" aria-busy="true" aria-live="polite">
        <div className="h-24 animate-pulse-soft rounded-lg border border-border bg-surface" />
        <div className="h-40 animate-pulse-soft rounded-lg border border-border bg-surface" />
      </div>
    );
  }
  if (state.error || state.data == null) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-fail/40 bg-fail-soft px-4 py-3 text-sm text-fail-ink"
      >
        {state.error ?? "No data available."}
      </div>
    );
  }
  return <>{children(state.data)}</>;
}
