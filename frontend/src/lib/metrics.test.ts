import { describe, expect, it } from "vitest";
import { formatMetricValue } from "@/lib/metrics";
import type { Metric } from "@/types/events";

describe("formatMetricValue", () => {
  it("renders em dash for pending metrics", () => {
    const m: Metric = {
      name: "rag_faithfulness",
      value: null,
      unit: "fraction",
      basis: "pending",
    };
    expect(formatMetricValue(m)).toBe("—");
  });

  it("formats measured fractions as percentages", () => {
    const m: Metric = {
      name: "policy_adherence",
      value: 0.822,
      unit: "fraction",
      basis: "measured",
    };
    expect(formatMetricValue(m)).toBe("82%");
  });
});
