import { EmptyState, Panel } from "@/components/ui/Panel";
import { Badge } from "@/components/ui/Badge";
import type { ExtractionMeta } from "@/types/events";

/**
 * The extraction *process* — how the fields were read, not just what they are.
 *
 * Two real stages, shown in order: the Docling parse (a structural text-layer
 * read, or an OCR read if the document had no usable text; whether it escalated
 * to OCR) and the Bedrock extraction (which model, whether page images were
 * attached to the vision call or it went text-only, prompt version, how many
 * schema-repair attempts it took, and the token cost). Every value is real,
 * recorded from the run; a value the backend didn't record is simply omitted
 * rather than guessed. Absent entirely on the scripted demo path, which never
 * parsed a document — stated plainly rather than shown as blank.
 */
export function ExtractionProcess({ meta }: { meta: ExtractionMeta | null }) {
  if (!meta) {
    return (
      <Panel title="Extraction process" subtitle="How the fields were read">
        <EmptyState
          title="No extraction process recorded"
          hint="The two-stage Docling parse → Bedrock extraction detail appears here for a run that read a real uploaded document."
        />
      </Panel>
    );
  }

  const ocr = meta.parse_strategy === "ocr" || meta.escalated_to_ocr === true;

  return (
    <Panel title="Extraction process" subtitle="How the fields were read">
      <div className="flex flex-col">
        <Stage
          step={1}
          title="Parse"
          engine={meta.parser_name ?? "Docling"}
          version={meta.parser_version}
          rows={[
            {
              label: "Strategy",
              value: (
                <Badge tone={ocr ? "flag" : "pass"}>
                  {meta.parse_strategy === "ocr"
                    ? "OCR"
                    : meta.parse_strategy === "structural"
                      ? "Structural text layer"
                      : (meta.parse_strategy ?? "—")}
                </Badge>
              ),
            },
            ...(meta.escalated_to_ocr
              ? [
                  {
                    label: "OCR escalation",
                    value: (
                      <span className="text-flag-ink">
                        Escalated (no usable text layer)
                      </span>
                    ),
                  },
                ]
              : []),
            optional("Pages", meta.page_count),
            optional("Text recovered", chars(meta.text_length)),
            optional("Parse time", ms(meta.parse_duration_ms)),
          ]}
        />

        <div className="ml-7 h-3 w-px bg-border" aria-hidden />

        <Stage
          step={2}
          title="Extract"
          engine="Amazon Bedrock"
          version={meta.model_id}
          rows={[
            {
              label: "Vision input",
              value:
                (meta.images_attached ?? 0) > 0 ? (
                  <Badge tone="brand">
                    {meta.images_attached} page image
                    {meta.images_attached === 1 ? "" : "s"} sent
                  </Badge>
                ) : (
                  <span className="text-ink-muted">Text-only (no page image)</span>
                ),
            },
            optional("Prompt version", meta.prompt_version),
            attempts(meta.attempts_used),
            optional(
              "Tokens",
              meta.input_tokens != null || meta.output_tokens != null
                ? `${meta.input_tokens ?? 0} in · ${meta.output_tokens ?? 0} out`
                : null,
            ),
            optional("Extract time", ms(meta.extract_duration_ms)),
          ]}
        />
      </div>
    </Panel>
  );
}

function Stage({
  step,
  title,
  engine,
  version,
  rows,
}: {
  step: number;
  title: string;
  engine: string;
  version?: string | null | undefined;
  rows: (Row | null)[];
}) {
  const shown = rows.filter((r): r is Row => r !== null);
  return (
    <section className="px-4 py-3">
      <header className="flex items-center gap-2.5">
        <span
          className="grid size-6 shrink-0 place-items-center rounded-full bg-brand-soft font-mono text-xs font-semibold text-brand-ink"
          aria-hidden
        >
          {step}
        </span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">
            {title}{" "}
            <span className="font-normal text-ink-subtle">· {engine}</span>
          </h3>
          {version && (
            <p className="truncate font-mono text-[11px] text-ink-subtle">
              {version}
            </p>
          )}
        </div>
      </header>
      <dl className="mt-2 flex flex-col gap-1.5 pl-[2.1rem]">
        {shown.map((r) => (
          <div key={r.label} className="flex items-center justify-between gap-3">
            <dt className="text-xs text-ink-subtle">{r.label}</dt>
            <dd className="text-right text-xs text-ink">{r.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

interface Row {
  label: string;
  value: React.ReactNode;
}

function optional(label: string, value: string | number | null | undefined): Row | null {
  if (value == null || value === "") return null;
  return { label, value: <span className="font-mono">{value}</span> };
}

function attempts(n: number | null | undefined): Row | null {
  if (n == null) return null;
  return {
    label: "Schema-repair attempts",
    value:
      n <= 1 ? (
        <span className="font-mono text-pass-ink">first try</span>
      ) : (
        <span className="font-mono text-flag-ink">{n} attempts</span>
      ),
  };
}

function ms(v: number | null | undefined): string | null {
  if (v == null) return null;
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;
}

function chars(v: number | null | undefined): string | null {
  if (v == null) return null;
  return `${v.toLocaleString()} chars`;
}
