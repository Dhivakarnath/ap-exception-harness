# ADR-013: Two-stage document extraction (Docling + Bedrock)

## Status
Accepted

## Context

Invoice ingestion must turn arbitrary PDFs and images into a typed, auditable
`Invoice`. A single “send the PDF to the model” approach is expensive, loses
table structure, and cannot highlight extracted fields back to source regions
(FR-2.5).

Invoices are **structured data in unstructured containers**: line-item tables,
header key-value pairs, totals blocks. Flat OCR merges columns and separates
labels from values — exactly the structure extraction needs.

## Decision

Split extraction into two deterministic stages before routing:

| Stage | Component | Role |
|-------|-----------|------|
| **Parse** | Docling (CPU, deterministic) | Layout, tables, text; OCR only when the text layer is missing |
| **Extract** | Bedrock (model) | Map parsed content (+ optional page images) into schema-constrained `Invoice` |

```
PDF / image  →  Docling parse  →  Bedrock extraction  →  canonical Invoice
                 (structural              (schema-constrained
                  or OCR)                  multimodal read)
```

**Docling** answers: *what text and layout is on the page?*

**Bedrock** answers: *what do those regions mean as invoice fields?*

### Parse strategy (Docling)

- Attempt a **cheap structural pass** first (read the PDF text layer).
- Escalate to **OCR** only when yield is implausibly low (< 120 characters).
- Images skip straight to OCR.
- Record `parse_strategy` (`structural` vs `ocr`) for cost attribution and the
  transparency UI.

### Extract strategy (Bedrock)

- Schema-constrained output (`RawInvoiceExtraction`); **no coercion** on parse
  failure (FR-2.4).
- Attach page images **conditionally** — when OCR was used or embedded pictures
  may add signal; not on every digital PDF.
- Wrap each field in `Extracted[T]` with confidence, method, and `SourceRegion`
  resolved from Docling provenance.

Neither stage decides PO vs non-PO routing. That is a pure read of
`Invoice.is_non_po` after extraction completes ([ADR-012](ADR-012-po-vs-non-po-routing.md)).

## Consequences

- **Lower cost on digital PDFs.** OCR is not run when the text layer is sufficient.
- **Better line-item fidelity.** Tables are rendered as grids before the model sees them.
- **Reviewable extraction.** Bounding boxes and element refs enable highlight-back in the UI.
- **Fail loud.** A value that will not parse into its canonical type aborts the run — no silent defaults.
- **Extraction is evaluated separately from agent evals.** CI grades field accuracy against manifest ground truth (`make eval`); live per-upload DeepEval does not re-score extraction field-by-field ([ADR-014](ADR-014-live-evaluation-scoring.md)).

## References

- `backend/ap_agent/ingest/parsing.py` — Docling parse, OCR escalation, regions
- `backend/ap_agent/extract/extractor.py` — Bedrock extraction, conditional images
- `backend/evals/extraction_eval.py` — CI extraction accuracy
- FR-2.4 (no coercion), FR-2.5 (source regions), FR-15.3 (context budget)
