# v1 technical essay

The post is [`where-the-model-decides.md`](where-the-model-decides.md).

Figures are embedded as PNG in the essay. Native draw.io sources stay here if a diagram needs an edit (open in [diagrams.net](https://app.diagrams.net/), File → Open from → Device, then re-export PNG over the matching file).

| File | In the essay? | Caption |
|------|---------------|---------|
| [`figures/AP-1.drawio.png`](figures/AP-1.drawio.png) | Yes — Figure 1 | Who owns the decision |
| [`figures/AP-2.drawio.png`](figures/AP-2.drawio.png) | Yes — Figure 2 | Gated vs reported vs cited |
| [`figures/the-line.drawio`](figures/the-line.drawio) | Source only | Editable original for AP-1 |
| [`figures/how-we-measure.drawio`](figures/how-we-measure.drawio) | Source only | Editable original for AP-2 |
| [`figures/application_images/Runs-page.png`](figures/application_images/Runs-page.png) | Yes | Route and status as recorded |
| [`figures/application_images/Reviews-page.png`](figures/application_images/Reviews-page.png) | Yes | HITL queue, human actions of record |
| [`figures/application_images/Evals-page.png`](figures/application_images/Evals-page.png) | Yes | Manifest gate beside live argument 50% |
| [`figures/application_images/Ploicy-page.png`](figures/application_images/Ploicy-page.png) | No | YAML pack UI; restates the $1,000 ceiling already in prose |
| [`figures/application_images/Guardrails-page.png`](figures/application_images/Guardrails-page.png) | No | In-app L1/L2/L3 explainer; already in the write path |
| [`figures/application_images/Ops-page.png`](figures/application_images/Ops-page.png) | No | Duplicates Runs; STP-vs-industry chrome fights the labeled-numbers rule |

Numbers in the essay are taken from `docs/roi-framing.md`, `docs/adr/ADR-001-system-architecture.md`, `docs/post-build-report.md`, and `backend/evals/results.json`. Do not mix gated scores with cited industry benchmarks in a headline. Industry URLs belong at the first mention in the body, not as a dump at the end.

## How readers open the ADRs (do not copy them into the post)

The professional receipt is that the decision records already exist in `docs/`. Copying them into `blog/` would create a second source of truth and look like they were written for the essay.

GitHub renders `.md` files as pages. When this repo is on GitHub, a reader of [`where-the-model-decides.md`](where-the-model-decides.md) clicks a Sources link such as `../docs/adr/ADR-001-system-architecture.md` and lands on the rendered ADR. Cursor’s markdown preview does the same locally.

If you later paste the essay onto a host that is *not* this repo (Hashnode, a company blog), those relative links break. Replace each `../docs/...` href with the GitHub blob URL for that file, for example:

`https://github.com/<org>/<repo>/blob/main/docs/adr/ADR-001-system-architecture.md`

Do not upload the ADRs as separate blog posts or CMS attachments. The repo *is* the host.
