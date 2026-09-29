# Datasets

## `generated/` — the adversarial invoice dataset

Generated, not committed (see `.gitignore`). Rebuild with:

```bash
make dataset          # 50 cases per failure mode (the FR-13.2 floor)
make dataset-quick    # 3 per mode, for iteration only
make dataset-verify   # check artefacts against the manifest
```

Output:

| Path | Contents |
|---|---|
| `generated/invoices/{tenant_id}/` | Rendered artefacts per tenant (`retail-demo`, `manufacturing-demo`) |
| `generated/manifest.json` | Ground truth: expected extraction, per-check verdicts, expected route, content hash |

Generator source: [`backend/apfixtures/`](../backend/apfixtures).

### Composition

23 failure modes × 50 cases. Three render formats, because each exercises a
different parsing path:

| Format | Count | Path exercised |
|---|---|---|
| `pdf_text` | 1,050 | Docling structural parse of a real text layer |
| `pdf_scanned` | 50 | OCR — the page is rasterised with **no** text layer |
| `image` | 50 | OCR from a bare PNG, as though photographed |

Two tenants are represented (`manufacturing-demo` 900, `retail-demo` 250) so the
same engine is exercised against two materially different policy packs.

### Determinism

Generation is reproducible byte for byte. Every case draws randomness from
`SeedSequence(seed, case_ordinal)`, and PDFs are written with reportLab's
`invariant=1` so no timestamp or document ID leaks in. A 3-case run is a true
prefix of a 50-case run.

This matters because the manifest stores a `content_sha256` per artefact. If
generation were non-deterministic, the drift check could never distinguish a
stale manifest from ordinary churn, and an eval regression could never be
distinguished from dataset noise. See `INC-003` in
[`docs/incident-log.md`](../docs/incident-log.md).

### Read this before quoting any number from this dataset

**The route distribution is deliberately not representative of production.**

| Expected route | Cases | Share |
|---|---|---|
| `hold` | 450 | 39% |
| `route_for_approval` | 400 | 35% |
| `auto_approve` | 200 | 17% |
| `reject` | 100 | 9% |

83% of cases require human review. Real AP traffic is the opposite shape —
industry reports put touchless processing at roughly 32% on average and near 49%
for top performers, meaning most invoices are *clean*.

The imbalance is intentional: this is an exam, not a traffic sample. Each failure
mode gets equal weight so no control is under-tested, and rare-but-costly cases
(bank-detail changes, duplicate resubmissions, threshold avoidance) appear often
enough to measure.

The consequence, stated plainly:

- **A straight-through-processing rate measured on this dataset is not a
  production STP estimate.** It is a measurement of behaviour on a deliberately
  adversarial mix. Reporting it as though it predicted production would be
  dishonest.
- Metrics that *are* meaningful here: per-failure-mode accuracy, extraction
  accuracy, check-verdict agreement, routing correctness, cost per invoice, and
  latency.
- To estimate a production STP rate you would need to weight per-mode results by
  a realistic mode frequency distribution — which this project does not claim to
  know.

### Ground truth

Each manifest entry carries:

- `expected_extraction` — field values a correct extraction must recover. Amounts
  are **strings**, so exact decimals survive JSON (a float would reintroduce the
  rounding error the domain layer forbids).
- `expected_checks` — the verdict each named check should reach. Checks are named,
  not tied to implementations, so the dataset stays valid across refactors.
  `skip` is used where a check does not apply (three-way match on a non-PO
  invoice), which is deliberately distinct from `pass`.
- `expected_route`, `requires_human_review`, `expected_gl_account`
- `min_extraction_confidence` — a floor for degraded inputs, not a promise of
  perfection.
- `rationale` — why this outcome is correct, in prose.

### Agreement with the mock ERP

Cases reference ledger objects by ID (`PO-2001`, `GRN-3005`, `V-1006`, paid bill
`INV-77001`). The generator does not import the ERP seed module; instead
`tests/unit/test_fixtures_dataset.py::TestSeedAgreement` asserts the two agree on
vendors, bank fingerprints, PO quantities and prices, receipt quantities, and the
chart of accounts. If the ledger seed drifts, those tests fail rather than the
dataset silently testing the wrong thing.

## `corpus/` — RAG corpus for GL coding

Accounting policy, vendor contracts, and coding precedent. Populated in Slice 7.

## `fixtures/` — hand-authored fixtures

Small documents used by targeted unit tests, committed where they are stable.
