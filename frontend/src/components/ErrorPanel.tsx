import { Panel } from "@/components/ui/Panel";
import type { ErrorEntry } from "@/store/runState";

/**
 * The error panel (design §12.2, FR-8.4). Failures are rendered prominently and
 * **unsoftened** — the stage, the exception type, the message, and the trace id,
 * in the fail color, with no reassuring spinner or vague "something went wrong".
 * A run that failed loud should look like it failed loud. Renders nothing when
 * there are no errors (it must not occupy space or imply a problem that is not
 * there).
 */
export function ErrorPanel({
  errors,
  traceId,
}: {
  errors: ErrorEntry[];
  traceId: string | null;
}) {
  if (errors.length === 0) return null;

  return (
    <Panel
      title="Failures"
      className="border-fail/50"
      trailing={
        <span className="font-mono text-xs text-fail-ink">
          {errors.length} error{errors.length === 1 ? "" : "s"}
        </span>
      }
    >
      <ul className="divide-y divide-fail/20" role="alert">
        {errors.map((err) => (
          <li key={err.seq} className="bg-fail-soft/40 px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-semibold text-fail-ink">
                {err.error_type ?? "Error"}
              </span>
              {err.stage && (
                <span className="text-xs text-ink-muted">at {err.stage}</span>
              )}
            </div>
            <p className="mt-1 text-sm leading-relaxed text-ink">{err.message}</p>
            {(err.trace_id ?? traceId) && (
              <p className="mt-1.5 font-mono text-[11px] text-ink-subtle">
                trace {err.trace_id ?? traceId}
              </p>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
