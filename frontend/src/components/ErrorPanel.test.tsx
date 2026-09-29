import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ErrorPanel } from "@/components/ErrorPanel";
import type { ErrorEntry } from "@/store/runState";

describe("ErrorPanel", () => {
  it("renders nothing when there are no errors (never implies a problem)", () => {
    const { container } = render(<ErrorPanel errors={[]} traceId={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a failure loudly with type, stage, message, and trace id", () => {
    const err: ErrorEntry = {
      seq: 3,
      at: null,
      error_type: "ExtractionError",
      message: "The model returned no parsed extraction.",
      stage: "extract",
      trace_id: "cf149e69",
    };
    render(<ErrorPanel errors={[err]} traceId="cf149e69" />);
    // role=alert so it is announced, not silently shown.
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("ExtractionError")).toBeInTheDocument();
    expect(screen.getByText(/at extract/)).toBeInTheDocument();
    expect(
      screen.getByText(/model returned no parsed extraction/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/trace cf149e69/)).toBeInTheDocument();
  });

  it("falls back to the run trace id when an error carries none", () => {
    const err: ErrorEntry = {
      seq: 1,
      at: null,
      message: "boom",
      trace_id: null,
    };
    render(<ErrorPanel errors={[err]} traceId="run-trace-99" />);
    expect(screen.getByText(/trace run-trace-99/)).toBeInTheDocument();
  });
});
