import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewsPage } from "@/pages/ReviewsPage";
import { TenantFilterProvider } from "@/store/useTenantFilter";
import type { HitlReviewSummary } from "@/transport/api";

vi.mock("@/transport/api", () => ({
  listHitlReviews: vi.fn(),
  submitReview: vi.fn(),
}));

import { listHitlReviews, submitReview } from "@/transport/api";

const review: HitlReviewSummary = {
  id: "rev-1",
  run_id: "ef252380-aadc-4f00-0000-000000000000",
  tenant_id: "retail-demo",
  invoice_id: "inv-1",
  reason: "Amount 3897.00 USD exceeds the touchless ceiling 1000.00 USD.",
  required_tier: "regional_manager",
  channel: "console",
  status: "pending",
  requested_at: "2026-09-26T05:27:18Z",
  decided_at: null,
  decided_by: null,
  gl_account: "5000",
};

function renderQueue() {
  return render(
    <MemoryRouter>
      <TenantFilterProvider>
        <ReviewsPage />
      </TenantFilterProvider>
    </MemoryRouter>,
  );
}

describe("ReviewsPage", () => {
  beforeEach(() => {
    vi.mocked(listHitlReviews).mockResolvedValue([review]);
    vi.mocked(submitReview).mockResolvedValue({
      run_id: review.run_id,
      route: "route_for_approval",
    });
  });

  it("offers approve, edit GL, and reject on each pending review", async () => {
    const user = userEvent.setup();
    renderQueue();

    expect(await screen.findByText(/exceeds the touchless ceiling/)).toBeInTheDocument();
    expect(screen.getByText("5000")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit GL" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit GL" }));
    const input = screen.getByRole("textbox", { name: "Edited GL account" });
    expect(input).toHaveValue("5000");
    await user.clear(input);
    await user.type(input, "6500");
    await user.click(screen.getByRole("button", { name: "Save & approve" }));

    expect(submitReview).toHaveBeenCalledWith(review.run_id, {
      decision: "edit",
      approver_identity: "reviewer@retail-demo",
      edited_gl_account: "6500",
    });
  });
});
