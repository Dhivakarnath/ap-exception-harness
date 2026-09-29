import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CheckLedger } from "@/components/CheckLedger";
import type { CheckEntry } from "@/store/runState";

function check(
  seq: number,
  name: string,
  verdict: CheckEntry["verdict"],
  extra: Partial<CheckEntry> = {},
): CheckEntry {
  return {
    seq,
    at: null,
    name,
    category: "integrity",
    verdict,
    severity: "info",
    reasoning: "because reasons",
    ...extra,
  };
}

describe("CheckLedger", () => {
  it("renders an empty state with no checks", () => {
    render(<CheckLedger checks={[]} live={false} />);
    expect(screen.getByText(/no checks yet/i)).toBeInTheDocument();
  });

  it("renders each check with its verdict, threshold, actual, and reasoning", () => {
    render(
      <CheckLedger
        checks={[
          check(0, "completeness", "pass", {
            threshold: "all present",
            actual: "all present",
          }),
          check(1, "three_way_match", "fail", {
            threshold: "within 5.00",
            actual: "off by 42.00",
          }),
        ]}
        live={false}
      />,
    );
    expect(screen.getByText("completeness")).toBeInTheDocument();
    expect(screen.getByText("three_way_match")).toBeInTheDocument();
    // Verdicts render as distinct badges.
    expect(screen.getByText("pass")).toBeInTheDocument();
    expect(screen.getByText("fail")).toBeInTheDocument();
    expect(screen.getByText("off by 42.00")).toBeInTheDocument();
    expect(screen.getAllByText(/because reasons/)).toHaveLength(2);
  });

  it("summarises passed count and exposes an aria-live region", () => {
    render(
      <CheckLedger
        checks={[check(0, "a", "pass"), check(1, "b", "flag")]}
        live
      />,
    );
    expect(screen.getByText(/1\/2 passed/)).toBeInTheDocument();
    // The streaming list is an ARIA live region so a screen reader announces
    // each check as it arrives.
    const region = screen.getByLabelText(/deterministic check results/i);
    expect(region).toHaveAttribute("aria-live", "polite");
  });

  it("marks a check that forces review", () => {
    render(
      <CheckLedger
        checks={[check(0, "bank_detail_change", "flag", { forces_review: true })]}
        live={false}
      />,
    );
    expect(screen.getByText(/escalates/i)).toBeInTheDocument();
  });

  it("groups checks by category with a section heading", () => {
    render(
      <CheckLedger
        checks={[
          check(0, "math_integrity", "pass", { category: "arithmetic" }),
          check(1, "duplicate_exact", "pass", { category: "duplicate" }),
        ]}
        live={false}
      />,
    );
    expect(
      screen.getByRole("heading", { name: /arithmetic/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /duplicate detection/i }),
    ).toBeInTheDocument();
  });

  it("keeps a check's inputs hidden until its row is expanded", async () => {
    const user = userEvent.setup();
    render(
      <CheckLedger
        checks={[
          check(0, "math_integrity", "pass", {
            category: "arithmetic",
            inputs: { computed_line_sum: "500.00 USD" },
          }),
        ]}
        live={false}
      />,
    );
    // Collapsed: the inputs table is not rendered yet.
    expect(screen.queryByText("500.00 USD")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /math integrity/i }));

    // Expanded: the exact input value the check used is now shown.
    expect(screen.getByText("500.00 USD")).toBeInTheDocument();
    expect(screen.getByText(/inputs the check used/i)).toBeInTheDocument();
  });

  it("explains a skip verdict as a precondition, on expand", async () => {
    const user = userEvent.setup();
    render(
      <CheckLedger
        checks={[
          check(0, "three_way_match", "skip", {
            category: "matching",
            reasoning: "No PO reference; three-way match not applicable.",
          }),
        ]}
        live={false}
      />,
    );
    // Skip reasoning is visible up-front.
    expect(screen.getByText(/three-way match not applicable/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /three way match/i }));
    expect(screen.getByText(/precondition not met/i)).toBeInTheDocument();
  });
});
