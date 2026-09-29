import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Sparkline } from "@/components/ui/Sparkline";

describe("Sparkline", () => {
  it("renders a polyline for two or more numeric points", () => {
    const { container } = render(<Sparkline points={[0.2, 0.5, 0.4]} />);
    expect(container.querySelector("polyline")).toBeTruthy();
  });

  it("returns null when fewer than two values exist", () => {
    const { container } = render(<Sparkline points={[null, null, 0.5]} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
