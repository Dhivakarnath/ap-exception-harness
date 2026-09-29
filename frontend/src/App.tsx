import { Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { TenantFilterProvider } from "@/store/useTenantFilter";
import { RunsPage } from "@/pages/RunsPage";
import { RunDetailPage } from "@/pages/RunDetailPage";
import { PolicyPage } from "@/pages/PolicyPage";
import { GuardrailsPage } from "@/pages/GuardrailsPage";
import { ObservabilityPage } from "@/pages/ObservabilityPage";
import { EvalsPage } from "@/pages/EvalsPage";
import { ReviewsPage } from "@/pages/ReviewsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

/**
 * The application root: the persistent shell (sidebar + chrome) wrapping the
 * routed pages. Routing is what turns the old single-page control room into a
 * navigable platform — each concern (runs, a run's detail, policy, guardrails,
 * observability, evals) is its own addressable route.
 */
export function App() {
  return (
    <TenantFilterProvider>
      <AppShell>
        <Routes>
        <Route path="/" element={<RunsPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
        <Route path="/reviews" element={<ReviewsPage />} />
        <Route path="/policy" element={<PolicyPage />} />
        <Route path="/guardrails" element={<GuardrailsPage />} />
        <Route path="/operations" element={<ObservabilityPage />} />
        <Route path="/observability" element={<ObservabilityPage />} />
        <Route path="/evals" element={<EvalsPage />} />
        <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </AppShell>
    </TenantFilterProvider>
  );
}
