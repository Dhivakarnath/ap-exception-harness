import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { EvalsContent } from "@/pages/EvalsPage";
import type {
  EvaluationBreakdown,
  EvalsReport,
  LiveEvaluation,
  ManifestScorecard,
} from "@/types/events";

const emptyBreakdown: EvaluationBreakdown = {
  tools: { selection: null, arguments: null, actual: [], expected: [] },
  rag: { faithfulness: null, answer_relevancy: null, contextual_relevancy: null },
  extraction: { matches: null, total: null, mismatches: [], case_id: null, source: null },
  policy: {
    matches: null,
    total: null,
    passed: null,
    flagged: null,
    failed: null,
    mismatches: [],
    case_id: null,
    source: null,
  },
};

function evaluation(overrides: Partial<LiveEvaluation> = {}): LiveEvaluation {
  return {
    run_id: "run-1",
    tenant_id: "retail-demo",
    invoice_id: "inv-1",
    invoice_number: "INV-100",
    vendor: "Acme",
    is_non_po: true,
    route: "route_for_approval",
    status: "completed",
    metrics: {
      task_completion: 0.92,
      tool_use: 1,
      rag_grounding: 0.84,
      extraction_accuracy: null,
      policy_adherence: 0.95,
    },
    breakdown: emptyBreakdown,
    rag_applicable: true,
    extraction_applicable: false,
    policy_applicable: true,
    evaluated_after_hitl: false,
    judge_model: "amazon.nova-lite-v1:0",
    error: null,
    started_at: "2026-09-15T08:00:00Z",
    evaluated_at: "2026-09-15T08:01:00Z",
    created_at: "2026-09-15T08:00:00Z",
    ...overrides,
  };
}

const sampleScorecard: ManifestScorecard = {
  status: "available",
  suite: "slice13_smoke_live",
  generated_at: "2026-09-16T07:34:07Z",
  policy_adherence: 1,
  routing_label_consistency: 1,
  pipeline_cases_passed: 30,
  pipeline_cases_total: 30,
  live: {
    task_completion: 0.925,
    tool_correctness: 1,
    argument_correctness: 0.75,
    rag_faithfulness: 1,
    rag_answer_relevancy: 1,
    rag_contextual_relevancy: 1,
    extraction_accuracy: 1,
  },
};

function renderEvals(report: EvalsReport) {
  return render(
    <MemoryRouter>
      <EvalsContent report={report} />
    </MemoryRouter>,
  );
}

describe("EvalsContent", () => {
  it("lists the run with number cards and grounds the open row per category", async () => {
    const user = userEvent.setup();
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [
        evaluation({
          breakdown: {
            tools: {
              selection: 1,
              arguments: 0,
              actual: ["erp.list_historical_bills"],
              expected: ["erp.list_historical_bills"],
            },
            rag: {
              faithfulness: 1,
              answer_relevancy: 0.84,
              contextual_relevancy: 0.9,
            },
          },
          metrics: {
            task_completion: 0.92,
            tool_use: 0,
            rag_grounding: 0.84,
          },
        }),
      ],
    });

    expect(screen.getByRole("button", { name: /INV-100/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    await user.click(screen.getByRole("button", { name: /INV-100/ }));

    expect(screen.getByRole("button", { name: /INV-100/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText("End-to-end completion")).toBeInTheDocument();
    expect(screen.getByText("Workflow judged")).toBeInTheDocument();
    expect(screen.getByText("Tool use")).toBeInTheDocument();
    expect(screen.getAllByText("Selection").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Arguments").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("RAG grounding")).toBeInTheDocument();
    expect(screen.getAllByText("Faithfulness").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Answer relevancy")).toBeInTheDocument();
    expect(screen.getByText("Contextual relevancy")).toBeInTheDocument();
    expect(screen.getByText("Called")).toBeInTheDocument();
    expect(screen.getAllByText("erp.list_historical_bills").length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText(/argument correctness scored 0%/i),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Policy adherence").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Extraction accuracy")).toBeInTheDocument();
  });

  it("shows tenant ground-truth and unknown extraction badges", () => {
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [
        evaluation({
          invoice_number: "INV-RETAIL",
          extraction_applicable: true,
          metrics: { extraction_accuracy: 1 },
          breakdown: {
            extraction: {
              matches: 7,
              total: 7,
              mismatches: [],
              case_id: "non_po_services-000",
              source: "manifest_ground_truth",
              ground_truth_status: "matched",
              ground_truth_tenant: "retail-demo",
            },
          },
        }),
        evaluation({
          run_id: "run-unknown",
          invoice_number: "INV-UNK",
          extraction_applicable: false,
          breakdown: {
            extraction: {
              matches: null,
              total: null,
              mismatches: [],
              case_id: null,
              source: null,
              ground_truth_status: "unknown",
              tenant_id: "retail-demo",
              reason: "No ground truth for this document in retail-demo.",
            },
          },
        }),
      ],
    });

    expect(screen.getByText("Retail ground truth")).toBeInTheDocument();
    expect(screen.getByText("Unknown document")).toBeInTheDocument();
  });

  it("labels RAG as not applicable for a PO-backed run", async () => {
    const user = userEvent.setup();
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [
        evaluation({
          run_id: "run-2",
          invoice_id: "inv-2",
          invoice_number: "INV-200",
          vendor: "Globex",
          is_non_po: false,
          route: "auto_approve",
          metrics: {
            task_completion: 1,
            tool_use: 1,
            rag_grounding: null,
          },
          rag_applicable: false,
        }),
      ],
    });

    expect(screen.getByText("PO-backed")).toBeInTheDocument();
    expect(screen.getAllByText("N/A").length).toBeGreaterThanOrEqual(1);

    await user.click(screen.getByRole("button", { name: /INV-200/ }));

    expect(
      screen.getByText(/GL was inherited from the purchase order/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Faithfulness")).not.toBeInTheDocument();
  });

  it("shows an honest empty state when no live document has been scored", () => {
    renderEvals({
      status: "not_run",
      message: "No live document has completed DeepEval scoring yet.",
      evaluations: [],
    });

    expect(screen.getByText("No evaluated documents yet")).toBeInTheDocument();
    expect(
      screen.getByText("No live document has completed DeepEval scoring yet."),
    ).toBeInTheDocument();
  });

  it("keeps pending scores as in-progress, not as zeros", () => {
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [
        evaluation({
          run_id: "run-pending",
          invoice_id: "inv-pending",
          invoice_number: "INV-300",
          vendor: "Initech",
          route: null,
          status: "running",
          metrics: {
            task_completion: null,
            tool_use: null,
            rag_grounding: null,
          },
          rag_applicable: false,
          evaluated_at: null,
        }),
      ],
    });

    expect(screen.getAllByText("Evaluating").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("…").length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(screen.queryByText("Selection")).not.toBeInTheDocument();
  });

  it("surfaces a failed judge loudly and keeps earlier completed runs in the list", async () => {
    const user = userEvent.setup();
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [
        evaluation({
          run_id: "run-fail",
          invoice_id: "inv-fail",
          invoice_number: "INV-400",
          vendor: "Soylent",
          route: null,
          status: "failed",
          metrics: {
            task_completion: null,
            tool_use: null,
            rag_grounding: null,
          },
          rag_applicable: false,
          error: "ThrottlingException: Bedrock rate exceeded",
        }),
        evaluation({
          run_id: "run-old",
          invoice_id: "inv-old",
          breakdown: {
            tools: {
              selection: 1,
              arguments: 0.67,
              actual: [],
              expected: [],
            },
            rag: {
              faithfulness: 0.84,
              answer_relevancy: 0.9,
              contextual_relevancy: 0.8,
            },
          },
        }),
      ],
    });

    expect(screen.getByText("Evaluated runs")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /INV-400/ }));

    expect(
      screen.getByText("Evaluation failed: ThrottlingException: Bedrock rate exceeded"),
    ).toBeInTheDocument();
    expect(screen.getByText("INV-100")).toBeInTheDocument();
    expect(screen.getAllByText("92%").length).toBeGreaterThanOrEqual(1);

    await user.click(screen.getByRole("button", { name: /INV-100/ }));

    expect(screen.getByRole("button", { name: /INV-100/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText("67%")).toBeInTheDocument();
  });

  it("lets every run stay collapsed at the same time", () => {
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [
        evaluation({ run_id: "run-a", invoice_number: "INV-A" }),
        evaluation({ run_id: "run-b", invoice_number: "INV-B" }),
      ],
    });

    expect(screen.getByRole("button", { name: /INV-A/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByRole("button", { name: /INV-B/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByText("End-to-end completion")).not.toBeInTheDocument();
  });

  it("explains manifest vs uploads and shows in-progress as a run count", () => {
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [
        evaluation({ status: "running", metrics: { task_completion: null, tool_use: null, rag_grounding: null } }),
        evaluation({ run_id: "run-2", invoice_number: "INV-2" }),
      ],
    });

    expect(screen.getByRole("heading", { name: "Uploaded documents" })).toBeInTheDocument();
    expect(screen.getByText("Summary counts — not percentages")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByText(/a count, not a quality %/)).toBeInTheDocument();
    const inProgressTile = screen.getByText("In progress").closest("div");
    expect(inProgressTile).toHaveTextContent("1");
    expect(inProgressTile).toHaveTextContent("run");
  });

  it("shows the manifest scorecard with policy first and routing consistency", () => {
    renderEvals({
      status: "available",
      manifest_scorecard: sampleScorecard,
      evaluations: [evaluation()],
    });

    expect(screen.getByRole("heading", { name: "Manifest eval gate" })).toBeInTheDocument();
    expect(screen.getAllByText("Policy adherence").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Routing label consistency").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Live Bedrock sample/)).toBeInTheDocument();
    expect(screen.getAllByText("100%").length).toBeGreaterThanOrEqual(3);
  });
});
