import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineFlow } from "@/components/PipelineFlow";
import type { StageEntry } from "@/store/runState";

function stage(name: string, status: StageEntry["status"]): StageEntry {
  return { name, status, seq: 0, at: null };
}

describe("PipelineFlow", () => {
  it("renders horizontal pipeline steps", () => {
    render(
      <PipelineFlow
        status="running"
        stages={[stage("extract", "running"), stage("extract", "ok")]}
      />,
    );
    expect(screen.getByText("Processing pipeline")).toBeInTheDocument();
    expect(screen.getByText("Extract")).toBeInTheDocument();
    expect(screen.getByText("Policy engine")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: /pipeline stages/i })).toBeInTheDocument();
  });
});
