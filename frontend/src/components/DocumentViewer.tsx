import { useState } from "react";
import { cn } from "@/lib/cn";
import { EmptyState, Panel } from "@/components/ui/Panel";
import type { ExtractionField } from "@/types/events";

/**
 * The document viewer — the highlight-back surface (design §12.3, FR-2.5).
 *
 * When the run has a resolvable source page (`imageUrl` — either a demo
 * fixture's representative page or an upload run's own uploaded file), this
 * renders that *real* rendered page — not a mock. Over it, it draws a bounding
 * box for every field that genuinely carries one, and selecting a field (here
 * or in Extraction) highlights its box. The caller resolves which endpoint
 * produces the image (fixture vs. this run's own document); this component
 * only renders whatever URL it is given.
 *
 * Honest scope: the scripted demo invoices are synthetic and carry no
 * geometry, so their fields have no `bbox`. Rather than fabricate boxes on a
 * page they were never read from, the viewer shows the real page plus a
 * provenance list of each field's Docling `element_ref`, and states plainly
 * that pixel boxes appear on a live parse (where the extractor recovers real
 * regions). Boxes are drawn if and only if the data is real.
 */
export function DocumentViewer({
  fields,
  selectedRef,
  onSelect,
  imageUrl,
}: {
  fields: ExtractionField[];
  selectedRef: string | null;
  onSelect: (regionRef: string | null) => void;
  imageUrl?: string | null;
}) {
  const boxed = fields.filter((f) => f.bbox);
  const located = fields.filter((f) => f.region_ref);

  if (!imageUrl && located.length === 0) {
    return (
      <Panel title="Source" subtitle="The document each field came from">
        <EmptyState
          title="No source document"
          hint="On a live model run, the rendered invoice appears here with each extracted field boxed on the page."
        />
      </Panel>
    );
  }

  return (
    <Panel
      title="Source document"
      subtitle={
        boxed.length > 0
          ? `${boxed.length} field${boxed.length === 1 ? "" : "s"} located on the page`
          : "Rendered source page · field provenance below"
      }
    >
      <div className="flex flex-col gap-3 p-4">
        {imageUrl ? (
          <DocumentImage
            key={imageUrl}
            src={imageUrl}
            fields={boxed}
            selectedRef={selectedRef}
            onSelect={onSelect}
          />
        ) : null}

        {boxed.length === 0 && (
          <div className="rounded-md border border-dashed border-border bg-surface-2 p-3">
            <p className="text-xs leading-relaxed text-ink-subtle">
              This is the rendered source page for the run's scenario. The
              scripted demo invoice carries no per-field geometry, so fields are
              listed by their source reference below rather than boxed on the
              page. On a live parse, each field is drawn as a box here.
            </p>
          </div>
        )}

        {located.length > 0 && (
          <ul className="flex flex-col gap-1.5">
            {located.map((field) => {
              const selected = field.region_ref === selectedRef;
              return (
                <li key={field.name}>
                  <button
                    type="button"
                    onClick={() =>
                      onSelect(selected ? null : (field.region_ref ?? null))
                    }
                    className={cn(
                      "flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                      selected
                        ? "border-brand bg-brand-soft/50"
                        : "border-border hover:bg-surface-2",
                    )}
                  >
                    <span className="truncate text-sm text-ink">
                      {field.value ?? "—"}
                    </span>
                    <span className="shrink-0 font-mono text-[11px] text-ink-subtle">
                      {field.region_ref}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Panel>
  );
}

/**
 * The rendered page with bbox overlays. The image sets the intrinsic size; the
 * boxes are absolutely positioned by normalized fractions (top-left origin,
 * matching the backend's `_normalise_bbox`), so they track the page at any
 * width. A load error is surfaced, never hidden behind a blank frame.
 */
function DocumentImage({
  src,
  fields,
  selectedRef,
  onSelect,
}: {
  src: string;
  fields: ExtractionField[];
  selectedRef: string | null;
  onSelect: (regionRef: string | null) => void;
}) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  return (
    <div className="relative overflow-hidden rounded-md border border-border bg-surface-2">
      {status === "error" ? (
        <div
          role="alert"
          className="px-4 py-6 text-center text-sm text-fail-ink"
        >
          Could not render the source page.
        </div>
      ) : (
        <div className="relative">
          <img
            src={src}
            alt="Rendered source invoice"
            className={cn(
              "block h-auto w-full transition-opacity",
              status === "ready" ? "opacity-100" : "opacity-0",
            )}
            onLoad={() => setStatus("ready")}
            onError={() => setStatus("error")}
          />
          {status === "loading" && (
            <div className="absolute inset-0 grid place-items-center">
              <span className="h-32 w-full animate-pulse-soft rounded bg-surface-3" />
            </div>
          )}
          {status === "ready" &&
            fields.map((field) => {
              const b = field.bbox!;
              const selected = field.region_ref === selectedRef;
              return (
                <button
                  key={field.name}
                  type="button"
                  onClick={() =>
                    onSelect(selected ? null : (field.region_ref ?? null))
                  }
                  title={`${field.name}: ${field.value ?? ""}`}
                  aria-label={`${field.name} on the page`}
                  className={cn(
                    "absolute rounded-sm border-2 transition-colors",
                    selected
                      ? "border-brand bg-brand/20"
                      : "border-flag/70 bg-flag/10 hover:bg-flag/20",
                  )}
                  style={{
                    left: `${b.left * 100}%`,
                    top: `${b.top * 100}%`,
                    width: `${(b.right - b.left) * 100}%`,
                    height: `${(b.bottom - b.top) * 100}%`,
                  }}
                />
              );
            })}
        </div>
      )}
    </div>
  );
}
