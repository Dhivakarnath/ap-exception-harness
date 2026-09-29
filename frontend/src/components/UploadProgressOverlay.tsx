import { cn } from "@/lib/cn";

export type UploadPhase = "uploading" | "processing" | "starting";

const PHASES: { id: UploadPhase; label: string; detail: string }[] = [
  {
    id: "uploading",
    label: "Uploading document",
    detail: "Sending your file securely to the API",
  },
  {
    id: "processing",
    label: "Parsing & extracting",
    detail: "Docling structure pass and Bedrock field extraction",
  },
  {
    id: "starting",
    label: "Starting pipeline",
    detail: "Opening the live run stream",
  },
];

/**
 * Full-screen upload progress — keeps the UI responsive while a large PDF
 * uploads and the server performs parse + extract before returning a run id.
 */
export function UploadProgressOverlay({
  fileName,
  phase,
  uploadPercent,
}: {
  fileName: string;
  phase: UploadPhase;
  /** 0–100 during upload; null for indeterminate server work */
  uploadPercent: number | null;
}) {
  const phaseIndex = PHASES.findIndex((p) => p.id === phase);
  const overall =
    phase === "uploading" && uploadPercent != null
      ? Math.min(88, Math.round(uploadPercent * 0.88))
      : phase === "processing"
        ? 92
        : 98;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 px-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="upload-progress-title"
      aria-describedby="upload-progress-desc"
    >
      <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-lg">
        <p id="upload-progress-title" className="text-base font-semibold text-ink">
          Processing your invoice
        </p>
        <p id="upload-progress-desc" className="mt-1 truncate text-sm text-ink-muted">
          {fileName}
        </p>

        <div className="mt-5">
          <div
            className="h-2 overflow-hidden rounded-full bg-surface-3"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={overall}
            aria-label="Overall upload and processing progress"
          >
            <div
              className={cn(
                "h-full rounded-full bg-brand transition-[width] duration-300 ease-[var(--ease-out-quint)]",
                phase !== "uploading" && "animate-pulse-soft",
              )}
              style={{ width: `${overall}%` }}
            />
          </div>
          <p className="mt-2 text-right font-mono text-xs text-ink-subtle">{overall}%</p>
        </div>

        <ol className="mt-6 space-y-3" aria-label="Upload phases">
          {PHASES.map((item, index) => {
            const done = index < phaseIndex;
            const active = index === phaseIndex;
            return (
              <li
                key={item.id}
                className={cn(
                  "flex gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                  active
                    ? "border-brand/40 bg-brand-soft/40"
                    : done
                      ? "border-border bg-surface-2/50"
                      : "border-transparent opacity-60",
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold",
                    done
                      ? "bg-brand text-white"
                      : active
                        ? "bg-brand-soft text-brand-ink ring-2 ring-brand/30"
                        : "bg-surface-3 text-ink-subtle",
                  )}
                  aria-hidden
                >
                  {done ? "✓" : index + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">{item.label}</p>
                  <p className="text-xs text-ink-subtle">{item.detail}</p>
                  {active && item.id === "uploading" && uploadPercent != null ? (
                    <p className="mt-1 font-mono text-[11px] text-ink-muted">
                      Transfer {uploadPercent}%
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
